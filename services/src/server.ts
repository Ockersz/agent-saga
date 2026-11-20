import express, { Request, Response } from 'express';
import { v4 as uuidv4 } from 'uuid';

const app = express();
app.use(express.json());

// In-Memory Database State for Enterprise Microservices
interface DatabaseState {
  employees: Map<string, { employeeId: string; status: string; reason: string; updatedAt: Date }>;
  settlements: Map<string, { settlementId: string; employeeId: string; amount: number; currency: string; status: string }>;
  deliveries: Map<string, { deliveryId: string; employeeId: string; recipientEmail: string; status: string }>;
}

const db: DatabaseState = {
  employees: new Map(),
  settlements: new Map(),
  deliveries: new Map(),
};

// Seed baseline employee state
db.employees.set('EMP-00104', {
  employeeId: 'EMP-00104',
  status: 'ACTIVE',
  reason: 'Initial employment record',
  updatedAt: new Date(),
});

// Logging middleware
app.use((req, res, next) => {
  const forwardedBy = req.headers['x-forwarded-by'];
  const workflowId = req.headers['x-saga-workflow-id'];
  console.log(`[Services Mesh] ${req.method} ${req.url} (Forwarded: ${forwardedBy || 'Direct'}, Workflow: ${workflowId || 'None'})`);
  next();
});

// ==========================================
// 1. HR & Biometric Microservice
// ==========================================
app.post('/api/v1/hr/employees/:employeeId/status', (req: Request, res: Response) => {
  const employeeId = req.params.employeeId as string;
  const { status, reason } = req.body;

  const existing = db.employees.get(employeeId) || {
    employeeId,
    status: 'ACTIVE',
    reason: 'New record',
    updatedAt: new Date(),
  };

  const previousStatus = existing.status;
  existing.status = status;
  existing.reason = reason;
  existing.updatedAt = new Date();
  db.employees.set(employeeId, existing);

  console.log(`[HR Service] Employee ${employeeId} status changed from ${previousStatus} -> ${status}`);

  return res.status(200).json({
    employeeId,
    previousStatus,
    currentStatus: status,
    updatedAt: existing.updatedAt.toISOString(),
  });
});

// ==========================================
// 2. Payroll & Settlement Microservice
// ==========================================
app.post('/api/v1/payroll/settlement', (req: Request, res: Response) => {
  const { employeeId, amount, currency, settlementType, reason } = req.body;

  // Check for simulated downstream failure
  if (req.headers['x-simulate-failure'] === 'payroll' || req.body.simulateFailure === true) {
    console.error(`[Payroll Service] INJECTED FAILURE: Database lock timeout simulation.`);
    return res.status(500).json({
      error: 'InternalServerError',
      message: 'Simulated database deadlock during payroll settlement write.',
    });
  }

  const settlementId = uuidv4();
  db.settlements.set(settlementId, {
    settlementId,
    employeeId,
    amount,
    currency,
    status: 'POSTED',
  });

  console.log(`[Payroll Service] Recorded settlement ${settlementId} of ${amount} ${currency} for ${employeeId}`);

  return res.status(201).json({
    settlementId,
    employeeId,
    amount,
    currency,
    status: 'POSTED',
  });
});

app.post('/api/v1/payroll/settlement/:settlementId/reversal', (req: Request, res: Response) => {
  const settlementId = req.params.settlementId as string;
  const { reason } = req.body;

  const settlement = db.settlements.get(settlementId);
  if (!settlement) {
    return res.status(404).json({ error: 'SettlementNotFound', message: `Settlement ${settlementId} not found.` });
  }

  settlement.status = 'REVERSED';
  db.settlements.set(settlementId, settlement);

  console.warn(`[Payroll Service] SAGA REVERSAL EXECUTED: Settlement ${settlementId} is now REVERSED. Reason: ${reason}`);

  return res.status(200).json({
    settlementId,
    status: 'REVERSED',
    reversedAt: new Date().toISOString(),
    reason,
  });
});

// ==========================================
// 3. Notification & Clearance Microservice (Pivot Action)
// ==========================================
app.post('/api/v1/notifications/clearance-email', (req: Request, res: Response) => {
  const { employeeId, recipientEmail, settlementId, subject } = req.body;

  // Check for simulated downstream failure on the terminal pivot step
  if (req.headers['x-simulate-failure'] === 'notification' || req.body.simulateFailure === true) {
    console.error(`[Notification Service] INJECTED FAILURE: SMTP Relay Timeout 504.`);
    return res.status(504).json({
      error: 'GatewayTimeout',
      message: 'SMTP external dispatch server failed to acknowledge receipt.',
    });
  }

  const deliveryId = uuidv4();
  db.deliveries.set(deliveryId, {
    deliveryId,
    employeeId,
    recipientEmail,
    status: 'SENT',
  });

  console.log(`[Notification Service] PIVOT EXECUTED: Clearance letter dispatched to ${recipientEmail}`);

  return res.status(202).json({
    deliveryId,
    status: 'SENT',
  });
});

// ==========================================
// 4. Testbed Observability & State Inspection APIs
// ==========================================
app.get('/api/v1/state', (req: Request, res: Response) => {
  return res.json({
    employees: Object.fromEntries(db.employees),
    settlements: Object.fromEntries(db.settlements),
    deliveries: Object.fromEntries(db.deliveries),
  });
});

app.post('/api/v1/reset', (req: Request, res: Response) => {
  db.employees.clear();
  db.settlements.clear();
  db.deliveries.clear();
  db.employees.set('EMP-00104', {
    employeeId: 'EMP-00104',
    status: 'ACTIVE',
    reason: 'Initial employment record',
    updatedAt: new Date(),
  });
  console.log(`[Services Mesh] State reset to clean baseline.`);
  return res.json({ status: 'RESET_COMPLETED' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`🚀 Mock Enterprise Services Mesh listening on port ${PORT}`);
});
