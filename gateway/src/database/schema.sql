-- PostgreSQL DDL for AgentSaga Distributed Transaction Journal

CREATE TABLE IF NOT EXISTS saga_workflows (
    workflow_id VARCHAR(128) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'STARTED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS saga_step_journal (
    step_id VARCHAR(128) PRIMARY KEY,
    workflow_id VARCHAR(128) NOT NULL REFERENCES saga_workflows(workflow_id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    target_service VARCHAR(64) NOT NULL,
    target_path VARCHAR(255) NOT NULL,
    http_method VARCHAR(10) NOT NULL,
    action_type VARCHAR(20) NOT NULL, -- compensatable, pivot, retriable
    risk_level VARCHAR(20) NOT NULL,
    
    forward_path_params JSONB,
    forward_request_body JSONB NOT NULL,
    forward_response_body JSONB,
    forward_status_code INT,
    
    compensating_path VARCHAR(255),
    compensating_method VARCHAR(10),
    compensating_mapping JSONB,
    compensating_payload JSONB,
    
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    execution_latency_ms INT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_saga_workflow ON saga_step_journal(workflow_id, step_index);
