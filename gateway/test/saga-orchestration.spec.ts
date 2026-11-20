import { SagaJournalService } from '../src/saga/saga-journal.service';
import { SagaCompensatorService } from '../src/saga/saga-compensator.service';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.MockedFunction<typeof axios>;

describe('SagaOrchestration & Compensation', () => {
  let journal: SagaJournalService;
  let compensator: SagaCompensatorService;

  beforeEach(() => {
    jest.clearAllMocks();
    journal = new SagaJournalService();
    compensator = new SagaCompensatorService(journal);
  });

  it('should execute reverse-order compensation when a workflow encounters a downstream failure', async () => {
    const workflowId = 'test-wf-001';
    await journal.getOrCreateWorkflow(workflowId, 'agent-007');

    // Step 1: HR Status Update (Compensatable)
    const step1 = await journal.recordStep({
      workflowId,
      stepIndex: 1,
      targetService: 'HR Service',
      targetPath: '/api/v1/hr/employees/EMP-00104/status',
      httpMethod: 'POST',
      actionType: 'compensatable',
      riskLevel: 'medium',
      forwardRequestBody: { status: 'INACTIVE', reason: 'Resignation' },
      compensatingPath: '/api/v1/hr/employees/EMP-00104/status',
      compensatingMethod: 'POST',
      status: 'EXECUTED',
    });

    // Step 2: Payroll Settlement (Compensatable)
    const step2 = await journal.recordStep({
      workflowId,
      stepIndex: 2,
      targetService: 'Payroll Service',
      targetPath: '/api/v1/payroll/settlement',
      httpMethod: 'POST',
      actionType: 'compensatable',
      riskLevel: 'high',
      forwardRequestBody: { employeeId: 'EMP-00104', amount: 5000, currency: 'LKR' },
      compensatingPath: '/api/v1/payroll/settlement/reverse',
      compensatingMethod: 'POST',
      status: 'EXECUTED',
    });

    // Step 3: Notification Dispatch fails (Downstream 500)
    await journal.recordStep({
      workflowId,
      stepIndex: 3,
      targetService: 'Notification Service',
      targetPath: '/api/v1/notifications/clearance-email',
      httpMethod: 'POST',
      actionType: 'pivot',
      riskLevel: 'medium',
      forwardRequestBody: { employeeId: 'EMP-00104' },
      status: 'FAILED',
    });

    // Mock successful rollback responses for Step 2 and Step 1
    mockedAxios.mockResolvedValue({ status: 200, data: { success: true } });

    // Trigger compensation
    const result = await compensator.compensateWorkflow(workflowId, 'http://mock-service');

    expect(result.totalCompensated).toBe(2);
    expect(result.failedCompensations).toBe(0);

    // Verify reverse topological execution order: Step 2 rolled back BEFORE Step 1
    expect(mockedAxios).toHaveBeenCalledTimes(2);
    const firstCallConfig = mockedAxios.mock.calls[0][0] as any;
    const secondCallConfig = mockedAxios.mock.calls[1][0] as any;
    expect(firstCallConfig.url).toContain('/api/v1/payroll/settlement/reverse');
    expect(secondCallConfig.url).toContain('/api/v1/hr/employees/EMP-00104/status');
  });
});
