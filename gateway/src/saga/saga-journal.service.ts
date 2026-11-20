import { Injectable, Logger } from '@nestjs/common';
import { Pool } from 'pg';
import { v4 as uuidv4 } from 'uuid';

export interface WorkflowRecord {
  workflowId: string;
  agentId: string;
  sessionId: string;
  status: 'STARTED' | 'IN_PROGRESS' | 'COMPENSATING' | 'COMPENSATED' | 'COMMITTED' | 'FAILED';
  createdAt: Date;
  updatedAt: Date;
}

export interface StepRecord {
  stepId: string;
  workflowId: string;
  stepIndex: number;
  targetService: string;
  targetPath: string;
  httpMethod: string;
  actionType: 'compensatable' | 'pivot' | 'retriable';
  riskLevel: string;
  forwardRequestBody: any;
  forwardResponseBody?: any;
  forwardStatusCode?: number;
  compensatingPath?: string;
  compensatingMethod?: string;
  compensatingPayload?: any;
  status: 'PENDING' | 'VALIDATED' | 'EXECUTED' | 'COMPENSATION_PENDING' | 'COMPENSATED' | 'FAILED';
  executionLatencyMs?: number;
  createdAt: Date;
}

@Injectable()
export class SagaJournalService {
  private readonly logger = new Logger(SagaJournalService.name);
  private pool?: Pool;
  
  // High-performance in-memory fallback for benchmarks and standalone runs
  private readonly memoryWorkflows = new Map<string, WorkflowRecord>();
  private readonly memorySteps = new Map<string, StepRecord[]>();

  constructor() {
    const dbUrl = process.env.DATABASE_URL;
    if (dbUrl) {
      this.pool = new Pool({ connectionString: dbUrl });
      this.logger.log('Connected to PostgreSQL for SAGA Journal storage.');
    } else {
      this.logger.log('Running SAGA Journal in high-performance in-memory mode.');
    }
  }

  public async getOrCreateWorkflow(workflowId: string, agentId = 'default-agent', sessionId = 'default-session'): Promise<WorkflowRecord> {
    if (this.pool) {
      const res = await this.pool.query('SELECT * FROM saga_workflows WHERE workflow_id = $1', [workflowId]);
      if (res.rows.length > 0) {
        const row = res.rows[0];
        return {
          workflowId: row.workflow_id,
          agentId: row.agent_id,
          sessionId: row.session_id,
          status: row.status,
          createdAt: row.created_at,
          updatedAt: row.updated_at,
        };
      }

      await this.pool.query(
        'INSERT INTO saga_workflows (workflow_id, agent_id, session_id, status) VALUES ($1, $2, $3, $4)',
        [workflowId, agentId, sessionId, 'IN_PROGRESS']
      );
    }

    if (!this.memoryWorkflows.has(workflowId)) {
      const wf: WorkflowRecord = {
        workflowId,
        agentId,
        sessionId,
        status: 'IN_PROGRESS',
        createdAt: new Date(),
        updatedAt: new Date(),
      };
      this.memoryWorkflows.set(workflowId, wf);
      this.memorySteps.set(workflowId, []);
      return wf;
    }

    return this.memoryWorkflows.get(workflowId)!;
  }

  public async recordStep(step: Omit<StepRecord, 'stepId' | 'createdAt'>): Promise<StepRecord> {
    const stepId = uuidv4();
    const fullStep: StepRecord = {
      ...step,
      stepId,
      createdAt: new Date(),
    };

    if (this.pool) {
      await this.pool.query(
        `INSERT INTO saga_step_journal 
        (step_id, workflow_id, step_index, target_service, target_path, http_method, action_type, risk_level, forward_request_body, forward_response_body, forward_status_code, compensating_path, compensating_method, compensating_payload, status, execution_latency_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)`,
        [
          stepId,
          fullStep.workflowId,
          fullStep.stepIndex,
          fullStep.targetService,
          fullStep.targetPath,
          fullStep.httpMethod,
          fullStep.actionType,
          fullStep.riskLevel,
          JSON.stringify(fullStep.forwardRequestBody),
          fullStep.forwardResponseBody ? JSON.stringify(fullStep.forwardResponseBody) : null,
          fullStep.forwardStatusCode,
          fullStep.compensatingPath,
          fullStep.compensatingMethod,
          fullStep.compensatingPayload ? JSON.stringify(fullStep.compensatingPayload) : null,
          fullStep.status,
          fullStep.executionLatencyMs,
        ]
      );
    }

    const steps = this.memorySteps.get(fullStep.workflowId) || [];
    steps.push(fullStep);
    this.memorySteps.set(fullStep.workflowId, steps);

    return fullStep;
  }

  public async updateStepStatus(
    stepId: string,
    status: StepRecord['status'],
    responseBody?: any,
    statusCode?: number,
    latencyMs?: number
  ): Promise<void> {
    if (this.pool) {
      await this.pool.query(
        `UPDATE saga_step_journal 
         SET status = $1, forward_response_body = $2, forward_status_code = $3, execution_latency_ms = $4
         WHERE step_id = $5`,
        [status, responseBody ? JSON.stringify(responseBody) : null, statusCode, latencyMs, stepId]
      );
    }

    for (const [_, steps] of this.memorySteps.entries()) {
      const match = steps.find(s => s.stepId === stepId);
      if (match) {
        match.status = status;
        if (responseBody !== undefined) match.forwardResponseBody = responseBody;
        if (statusCode !== undefined) match.forwardStatusCode = statusCode;
        if (latencyMs !== undefined) match.executionLatencyMs = latencyMs;
        break;
      }
    }
  }

  public async getExecutedSteps(workflowId: string): Promise<StepRecord[]> {
    if (this.pool) {
      const res = await this.pool.query(
        `SELECT * FROM saga_step_journal 
         WHERE workflow_id = $1 AND status = 'EXECUTED' 
         ORDER BY step_index DESC`,
        [workflowId]
      );
      return res.rows.map(row => ({
        stepId: row.step_id,
        workflowId: row.workflow_id,
        stepIndex: row.step_index,
        targetService: row.target_service,
        targetPath: row.target_path,
        httpMethod: row.http_method,
        actionType: row.action_type,
        riskLevel: row.risk_level,
        forwardRequestBody: row.forward_request_body,
        forwardResponseBody: row.forward_response_body,
        forwardStatusCode: row.forward_status_code,
        compensatingPath: row.compensating_path,
        compensatingMethod: row.compensating_method,
        compensatingPayload: row.compensating_payload,
        status: row.status,
        executionLatencyMs: row.execution_latency_ms,
        createdAt: row.created_at,
      }));
    }

    const steps = this.memorySteps.get(workflowId) || [];
    return steps
      .filter(s => s.status === 'EXECUTED')
      .sort((a, b) => b.stepIndex - a.stepIndex); // Reverse topological order
  }

  public async updateWorkflowStatus(workflowId: string, status: WorkflowRecord['status']): Promise<void> {
    if (this.pool) {
      await this.pool.query(
        'UPDATE saga_workflows SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE workflow_id = $2',
        [status, workflowId]
      );
    }

    const wf = this.memoryWorkflows.get(workflowId);
    if (wf) {
      wf.status = status;
      wf.updatedAt = new Date();
    }
  }
}
