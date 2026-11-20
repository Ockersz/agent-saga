import { Injectable, Logger } from '@nestjs/common';
import { EndpointContract } from '../contracts/contract-registry.service';

export interface PolicyEvaluationResult {
  allowed: boolean;
  reason?: string;
  ruleViolated?: 'MONETARY_THRESHOLD' | 'CRITICAL_RISK_ESCALATION' | 'AGENT_DENYLIST';
  policyLatencyMs: number;
}

@Injectable()
export class PolicyEvaluatorService {
  private readonly logger = new Logger(PolicyEvaluatorService.name);

  public evaluate(
    contract: EndpointContract,
    body: any,
    headers: Record<string, any> = {}
  ): PolicyEvaluationResult {
    const startTime = performance.now();
    const agentId = String(headers['x-agent-id'] || '').toLowerCase();
    const approvalToken = headers['x-agentsaga-approval-token'];

    // 1. Agent identity denylist
    if (agentId.startsWith('untrusted-') || agentId.startsWith('blocked-')) {
      const elapsed = performance.now() - startTime;
      this.logger.warn(`Policy blocked untrusted agent identity: ${agentId}`);
      return {
        allowed: false,
        ruleViolated: 'AGENT_DENYLIST',
        reason: `Agent identity '${agentId}' is explicitly denied by gateway security policy.`,
        policyLatencyMs: elapsed,
      };
    }

    // 2. Monetary invariant guard
    const maxAutonomousAmount = Number(process.env.POLICY_MAX_AMOUNT || 500000);
    if (typeof body?.amount === 'number' && body.amount > maxAutonomousAmount && !approvalToken) {
      const elapsed = performance.now() - startTime;
      this.logger.warn(
        `Policy blocked monetary mutation (${body.amount} > ${maxAutonomousAmount}) without approval token.`
      );
      return {
        allowed: false,
        ruleViolated: 'MONETARY_THRESHOLD',
        reason: `Transaction amount ${body.amount} exceeds autonomous limit of ${maxAutonomousAmount}. Human approval token required.`,
        policyLatencyMs: elapsed,
      };
    }

    // 3. Critical risk escalation guard
    if (contract.riskLevel === 'critical' && !approvalToken) {
      const elapsed = performance.now() - startTime;
      this.logger.warn(`Policy blocked critical-risk endpoint [${contract.method}] ${contract.path}`);
      return {
        allowed: false,
        ruleViolated: 'CRITICAL_RISK_ESCALATION',
        reason: `Critical-risk operation [${contract.method}] ${contract.path} requires an explicit x-agentsaga-approval-token.`,
        policyLatencyMs: elapsed,
      };
    }

    return {
      allowed: true,
      policyLatencyMs: performance.now() - startTime,
    };
  }
}
