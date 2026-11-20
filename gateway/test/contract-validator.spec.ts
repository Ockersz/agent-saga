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
      amount: 45000.5,
      currency: 'LKR',
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Unreturned MacBook charger fee upon resignation',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', validPayload);
    expect(result.valid).toBe(true);
    expect(result.errors).toBeUndefined();
    expect(result.validationLatencyMs).toBeLessThan(10);
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

  it('should strictly reject string-for-number type drift without silent coercion', () => {
    const typeDriftPayload = {
      employeeId: 'EMP-00104',
      amount: '5000',
      currency: 'LKR',
      settlementType: 'EQUIPMENT_DEDUCTION',
      reason: 'Unreturned MacBook charger fee',
    };

    const result = validator.validateRequest('POST', '/api/v1/payroll/settlement', typeDriftPayload);
    expect(result.valid).toBe(false);
    expect(result.errors).toBeDefined();
    expect(result.errors![0].field).toBe('/amount');
    expect(result.errors![0].keyword).toBe('type');
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

  it('should extract valid path parameters and reject invalid path parameters', () => {
    const body = { status: 'INACTIVE', reason: 'Employee resigned cleanly' };

    const validRes = validator.validateRequest('POST', '/api/v1/hr/employees/EMP-00104/status', body);
    expect(validRes.valid).toBe(true);
    expect(validRes.pathParams).toEqual({ employeeId: 'EMP-00104' });

    const invalidRes = validator.validateRequest('POST', '/api/v1/hr/employees/BAD_ID/status', body);
    expect(invalidRes.valid).toBe(false);
    expect(invalidRes.errors![0].field).toContain('path/employeeId');
  });

  it('should intercept an agent payload with an unauthorized enum value for currency', () => {
    const invalidPayload = {
      employeeId: 'EMP-00104',
      amount: 5000,
      currency: 'BITCOIN',
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
