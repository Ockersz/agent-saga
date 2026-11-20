import { PolicyEvaluatorService } from '../src/policy/policy-evaluator.service';
import { EndpointContract, ContractRegistryService } from '../src/contracts/contract-registry.service';
import { ContractValidatorService } from '../src/validator/contract-validator.service';
import { SagaJournalService } from '../src/saga/saga-journal.service';
import { SagaCompensatorService } from '../src/saga/saga-compensator.service';
import { GatewayProxyController } from '../src/proxy/gateway-proxy.controller';
import * as path from 'path';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.MockedFunction<typeof axios>;

function createMockResponse() {
  const headers: Record<string, string> = {};
  const res: any = {
    statusCode: 200,
    body: undefined,
    headers,
    setHeader(k: string, v: string) {
      headers[k.toLowerCase()] = v;
    },
    status(code: number) {
      res.statusCode = code;
      return res;
    },
    json(data: any) {
      res.body = data;
      return res;
    },
  };
  return res;
}

describe('Policy Gate, Dynamic Compensation Mapping, and Pivot Invariants', () => {
  let registry: ContractRegistryService;
  let validator: ContractValidatorService;
  let policy: PolicyEvaluatorService;
  let journal: SagaJournalService;
  let compensator: SagaCompensatorService;
  let controller: GatewayProxyController;

  beforeEach(() => {
    jest.clearAllMocks();
    registry = new ContractRegistryService();
    registry.loadContracts(path.resolve(__dirname, '..', '..', 'contracts'));
    validator = new ContractValidatorService(registry);
    policy = new PolicyEvaluatorService();
    journal = new SagaJournalService();
    compensator = new SagaCompensatorService(journal);
    controller = new GatewayProxyController(validator, policy, journal, compensator);
  });

  it('should enforce monetary threshold, critical risk escalation, and agent denylist rules', () => {
    const contract: EndpointContract = {
      serviceName: 'Payroll Service',
      path: '/api/v1/payroll/settlement',
      method: 'POST',
      actionType: 'compensatable',
      riskLevel: 'high',
    };

    const overLimit = policy.evaluate(contract, { amount: 750000 }, { 'x-agent-id': 'hr-bot' });
    expect(overLimit.allowed).toBe(false);
    expect(overLimit.ruleViolated).toBe('MONETARY_THRESHOLD');

    const withApproval = policy.evaluate(
      contract,
      { amount: 750000 },
      { 'x-agent-id': 'hr-bot', 'x-agentsaga-approval-token': 'approved-by-cfo' }
    );
    expect(withApproval.allowed).toBe(true);

    const deniedAgent = policy.evaluate(contract, { amount: 1000 }, { 'x-agent-id': 'untrusted-external' });
    expect(deniedAgent.allowed).toBe(false);
    expect(deniedAgent.ruleViolated).toBe('AGENT_DENYLIST');

    const criticalContract: EndpointContract = { ...contract, riskLevel: 'critical' };
    const criticalBlocked = policy.evaluate(criticalContract, { amount: 1000 }, { 'x-agent-id': 'hr-bot' });
    expect(criticalBlocked.allowed).toBe(false);
    expect(criticalBlocked.ruleViolated).toBe('CRITICAL_RISK_ESCALATION');
  });

  it('should resolve $.response.body and $.request.path dynamic mappings during SAGA compensation', async () => {
    const workflowId = 'wf-dynamic-001';
    await journal.getOrCreateWorkflow(workflowId, 'hr-bot');

    await journal.recordStep({
      workflowId,
      stepIndex: 1,
      targetService: 'HR Service',
      targetPath: '/api/v1/hr/employees/EMP-00104/status',
      httpMethod: 'POST',
      actionType: 'compensatable',
      riskLevel: 'medium',
      forwardPathParams: { employeeId: 'EMP-00104' },
      forwardRequestBody: { status: 'INACTIVE', reason: 'Resignation' },
      compensatingPath: '/api/v1/hr/employees/{employeeId}/status',
      compensatingMethod: 'POST',
      compensatingMapping: {
        employeeId: '$.request.path.employeeId',
        status: 'ACTIVE',
        reason: 'Automated SAGA rollback due to downstream workflow abort',
      },
      status: 'EXECUTED',
    });

    await journal.recordStep({
      workflowId,
      stepIndex: 2,
      targetService: 'Payroll Service',
      targetPath: '/api/v1/payroll/settlement',
      httpMethod: 'POST',
      actionType: 'compensatable',
      riskLevel: 'high',
      forwardRequestBody: { employeeId: 'EMP-00104', amount: 15000, currency: 'LKR' },
      forwardResponseBody: { settlementId: '11111111-2222-3333-4444-555555555555', status: 'POSTED' },
      compensatingPath: '/api/v1/payroll/settlement/{settlementId}/reversal',
      compensatingMethod: 'POST',
      compensatingMapping: {
        settlementId: '$.response.body.settlementId',
        reason: 'Automated SAGA rollback due to downstream workflow abort',
      },
      status: 'EXECUTED',
    });

    mockedAxios.mockResolvedValue({ status: 200, data: { status: 'REVERSED' } });

    const res = await compensator.compensateWorkflow(workflowId, 'http://127.0.0.1:3000');
    expect(res.totalCompensated).toBe(2);
    expect(res.failedCompensations).toBe(0);

    const call0 = mockedAxios.mock.calls[0][0] as any;
    expect(call0.url).toBe('http://127.0.0.1:3000/api/v1/payroll/settlement/11111111-2222-3333-4444-555555555555/reversal');
    expect(call0.data).toEqual({ reason: 'Automated SAGA rollback due to downstream workflow abort' });

    const call1 = mockedAxios.mock.calls[1][0] as any;
    expect(call1.url).toBe('http://127.0.0.1:3000/api/v1/hr/employees/EMP-00104/status');
    expect(call1.data).toEqual({
      status: 'ACTIVE',
      reason: 'Automated SAGA rollback due to downstream workflow abort',
    });
  });

  it('should block pivot execution with 409 Conflict if antecedent steps failed or workflow is compensated', async () => {
    const workflowId = 'wf-pivot-guard';
    await journal.getOrCreateWorkflow(workflowId, 'hr-bot');
    await journal.recordStep({
      workflowId,
      stepIndex: 1,
      targetService: 'HR Service',
      targetPath: '/api/v1/hr/employees/EMP-00104/status',
      httpMethod: 'POST',
      actionType: 'compensatable',
      riskLevel: 'medium',
      forwardRequestBody: { status: 'INACTIVE', reason: 'Resignation' },
      status: 'FAILED',
    });

    const req: any = {
      method: 'POST',
      path: '/proxy/api/v1/notifications/clearance-email',
      headers: {
        'x-saga-workflow-id': workflowId,
        'x-saga-step-index': '3',
        'x-agent-id': 'hr-bot',
      },
      body: {
        employeeId: 'EMP-00104',
        recipientEmail: 'emp104@example.com',
        settlementId: '11111111-2222-3333-4444-555555555555',
        subject: 'Clearance Notice',
      },
    };
    const res = createMockResponse();

    await controller.handleProxy(req, res);
    expect(res.statusCode).toBe(409);
    expect(res.body.error).toBe('AgentSaga Pivot Invariant Violation');
    expect(mockedAxios).not.toHaveBeenCalled();

    // Also block once workflow is marked COMPENSATED
    await journal.updateWorkflowStatus(workflowId, 'COMPENSATED');
    const resAfter = createMockResponse();
    await controller.handleProxy(req, resAfter);
    expect(resAfter.statusCode).toBe(409);
    expect(resAfter.body.error).toBe('AgentSaga Workflow Aborted');
    expect(mockedAxios).not.toHaveBeenCalled();
  });
});
