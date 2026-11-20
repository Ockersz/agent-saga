import { ContractRegistryService } from '../src/contracts/contract-registry.service';
import { ContractValidatorService } from '../src/validator/contract-validator.service';
import * as path from 'path';

describe('ContractValidatorService', () => {
  let registry: ContractRegistryService;
  let validator: ContractValidatorService;

  beforeAll(() => {
    registry = new ContractRegistryService();
    const contractsDir = path.resolve(__dirname, '..', '..', 'contracts');
    registry.loadContracts(contractsDir);
    validator = new ContractValidatorService(registry);
  });

  it('should successfully validate a conformant agent settlement payload', () => {
    const validPayload = {
      employeeId: 'EMP-00104',
      amount: 45000.50,
      currency: 'LKR',
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Unreturned MacBook charger fee upon resignation',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', validPayload);
    expect(result.valid).toBe(true);
    expect(result.errors).toBeUndefined();
    expect(result.validationLatencyMs).toBeLessThan(10); // Under 10ms target
  });

  it('should intercept an agent payload with a missing required field (amount)', () => {
    const invalidPayload = {
      employeeId: 'EMP-00104',
      currency: 'LKR',
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Unreturned MacBook charger fee',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', invalidPayload);
    expect(result.valid).toBe(false);
    expect(result.errors).toBeDefined();
    expect(result.errors![0].message).toContain("must have required property 'amount'");
  });

  it('should intercept an agent payload with a regex pattern violation in employeeId', () => {
    const invalidPayload = {
      employeeId: 'INVALID_ID_FORMAT',
      amount: 5000,
      currency: 'LKR',
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Equipment fee',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', invalidPayload);
    expect(result.valid).toBe(false);
    expect(result.errors).toBeDefined();
    expect(result.errors![0].field).toBe('/employeeId');
  });

  it('should intercept an agent payload with an unauthorized enum value for currency', () => {
    const invalidPayload = {
      employeeId: 'EMP-00104',
      amount: 5000,
      currency: 'BITCOIN', // Disallowed enum
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Equipment fee',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', invalidPayload);
    expect(result.valid).toBe(false);
    expect(result.errors).toBeDefined();
    expect(result.errors![0].field).toBe('/currency');
  });

  it('should block an unregistered API call that has no contract', () => {
    const result = validator.validateRequest('DELETE', '/api/v1/unknown/endpoint', {});
    expect(result.valid).toBe(false);
    expect(result.errors![0].keyword).toBe('unregistered_route');
  });
});
