import { Injectable, Logger } from '@nestjs/common';
import axios from 'axios';
import * as http from 'http';
import * as https from 'https';
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
  private readonly httpAgent = new http.Agent({ keepAlive: true, maxSockets: 64 });
  private readonly httpsAgent = new https.Agent({ keepAlive: true, maxSockets: 64 });

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
      const { resolvedPath, payload } = this.resolveCompensatingCall(step);
      const rollbackUrl = `${baseUrl}${resolvedPath}`;
      const rollbackMethod = (step.compensatingMethod || 'POST').toLowerCase();

      this.logger.log(
        `Rolling back Step ${step.stepIndex} [${step.targetPath}] -> Invoking ${rollbackMethod.toUpperCase()} ${rollbackUrl}`
      );

      try {
        await axios({
          method: rollbackMethod,
          url: rollbackUrl,
          data: payload,
          headers: {
            'Content-Type': 'application/json',
            'x-forwarded-by': 'AgentSaga-Compensator',
            'x-saga-workflow-id': workflowId,
          },
          timeout: 5000,
          httpAgent: this.httpAgent,
          httpsAgent: this.httpsAgent,
        });

        await this.journal.updateStepStatus(step.stepId, 'COMPENSATED');
        result.totalCompensated++;
        result.details.push({
          stepId: step.stepId,
          path: resolvedPath,
          status: 'COMPENSATED',
        });
      } catch (err: any) {
        this.logger.error(
          `Compensation failed for step ${step.stepId} [${rollbackUrl}]: ${err.message}`
        );
        await this.journal.updateStepStatus(step.stepId, 'COMPENSATION_PENDING');
        result.failedCompensations++;
        result.details.push({
          stepId: step.stepId,
          path: resolvedPath,
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

  public resolveCompensatingCall(step: StepRecord): { resolvedPath: string; payload: any } {
    const resolvedValues: Record<string, any> = {};

    if (step.compensatingMapping) {
      for (const [key, expr] of Object.entries(step.compensatingMapping)) {
        resolvedValues[key] = this.resolveExpression(expr, step);
      }
    }

    const consumedPathParams = new Set<string>();
    const rawPath = step.compensatingPath || '';
    const resolvedPath = rawPath.replace(/\{([^}]+)\}/g, (match, paramName) => {
      if (resolvedValues[paramName] !== undefined) {
        consumedPathParams.add(paramName);
        return encodeURIComponent(String(resolvedValues[paramName]));
      }
      if (step.forwardPathParams && step.forwardPathParams[paramName] !== undefined) {
        consumedPathParams.add(paramName);
        return encodeURIComponent(String(step.forwardPathParams[paramName]));
      }
      return match;
    });

    if (step.compensatingPayload) {
      return { resolvedPath, payload: step.compensatingPayload };
    }

    if (step.compensatingMapping && Object.keys(step.compensatingMapping).length > 0) {
      const payload: Record<string, any> = {};
      for (const [key, value] of Object.entries(resolvedValues)) {
        if (!consumedPathParams.has(key)) {
          payload[key] = value;
        }
      }
      return { resolvedPath, payload };
    }

    return {
      resolvedPath,
      payload: {
        reason: 'Agent workflow aborted. Automated inverse compensation executed by AgentSaga-Gateway.',
        originalStepIndex: step.stepIndex,
        originalPath: step.targetPath,
      },
    };
  }

  private resolveExpression(expr: string, step: StepRecord): any {
    if (typeof expr !== 'string' || !expr.startsWith('$.')) {
      return expr;
    }

    if (expr.startsWith('$.request.path.')) {
      const key = expr.slice('$.request.path.'.length);
      return this.getByDotPath(step.forwardPathParams, key);
    }

    if (expr.startsWith('$.request.body.')) {
      const key = expr.slice('$.request.body.'.length);
      return this.getByDotPath(step.forwardRequestBody, key);
    }

    if (expr.startsWith('$.response.body.')) {
      const key = expr.slice('$.response.body.'.length);
      return this.getByDotPath(step.forwardResponseBody, key);
    }

    return undefined;
  }

  private getByDotPath(obj: any, dotPath: string): any {
    if (!obj || !dotPath) return undefined;
    return dotPath.split('.').reduce((acc, part) => (acc != null ? acc[part] : undefined), obj);
  }
}
