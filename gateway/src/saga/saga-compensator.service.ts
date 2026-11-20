import { Injectable, Logger } from '@nestjs/common';
import axios from 'axios';
import { SagaJournalService, StepRecord } from './saga-journal.service';

export interface CompensationResult {
  workflowId: string;
  totalCompensated: number;
  failedCompensations: number;
  details: Array<{
    stepId: string;
    path: string;
    status: 'COMPENSATED' | 'FAILED';
    error?: string;
  }>;
}

@Injectable()
export class SagaCompensatorService {
  private readonly logger = new Logger(SagaCompensatorService.name);

  constructor(private readonly journal: SagaJournalService) {}

  public async compensateWorkflow(workflowId: string, upstreamBaseUrl?: string): Promise<CompensationResult> {
    this.logger.warn(`Initiating SAGA compensation for workflow: ${workflowId}`);
    await this.journal.updateWorkflowStatus(workflowId, 'COMPENSATING');

    const executedSteps = await this.journal.getExecutedSteps(workflowId);
    const compensatableSteps = executedSteps.filter(
      s => s.actionType === 'compensatable' && s.compensatingPath
    );

    const result: CompensationResult = {
      workflowId,
      totalCompensated: 0,
      failedCompensations: 0,
      details: [],
    };

    const baseUrl = upstreamBaseUrl || process.env.UPSTREAM_SERVICES_URL || 'http://localhost:3000';

    for (const step of compensatableSteps) {
      const rollbackUrl = `${baseUrl}${step.compensatingPath}`;
      const rollbackMethod = (step.compensatingMethod || 'POST').toLowerCase();
      const rollbackPayload = this.resolveCompensatingPayload(step);

      this.logger.log(
        `Rolling back Step ${step.stepIndex} [${step.targetPath}] -> Invoking ${rollbackMethod.toUpperCase()} ${rollbackUrl}`
      );

      try {
        await axios({
          method: rollbackMethod,
          url: rollbackUrl,
          data: rollbackPayload,
          timeout: 5000,
        });

        await this.journal.updateStepStatus(step.stepId, 'COMPENSATED');
        result.totalCompensated++;
        result.details.push({
          stepId: step.stepId,
          path: step.compensatingPath!,
          status: 'COMPENSATED',
        });
      } catch (err: any) {
        this.logger.error(
          `Compensation failed for step ${step.stepId} [${rollbackUrl}]: ${err.message}`
        );
        result.failedCompensations++;
        result.details.push({
          stepId: step.stepId,
          path: step.compensatingPath!,
          status: 'FAILED',
          error: err.message,
        });
      }
    }

    const finalStatus = result.failedCompensations === 0 ? 'COMPENSATED' : 'FAILED';
    await this.journal.updateWorkflowStatus(workflowId, finalStatus);
    this.logger.log(`SAGA compensation finished for workflow: ${workflowId}. Status: ${finalStatus}`);

    return result;
  }

  private resolveCompensatingPayload(step: StepRecord): any {
    if (step.compensatingPayload) {
      return step.compensatingPayload;
    }

    return {
      reason: 'Agent workflow aborted. Automated inverse compensation executed by AgentSaga-Gateway.',
      originalStepIndex: step.stepIndex,
      originalPath: step.targetPath,
    };
  }
}
