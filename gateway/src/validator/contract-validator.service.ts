import { Injectable, Logger } from '@nestjs/common';
import { ContractRegistryService, EndpointContract } from '../contracts/contract-registry.service';

export interface ValidationResult {
  valid: boolean;
  contract?: EndpointContract;
  errors?: Array<{
    field: string;
    message: string;
    keyword: string;
    params?: any;
  }>;
  validationLatencyMs: number;
}

@Injectable()
export class ContractValidatorService {
  private readonly logger = new Logger(ContractValidatorService.name);

  constructor(private readonly registry: ContractRegistryService) {}

  public validateRequest(method: string, path: string, body: any): ValidationResult {
    const startTime = performance.now();
    const contract = this.registry.getContract(method, path);

    if (!contract) {
      const elapsed = performance.now() - startTime;
      return {
        valid: false,
        validationLatencyMs: elapsed,
        errors: [
          {
            field: 'route',
            message: `Unregistered endpoint: [${method.toUpperCase()}] ${path}. No OpenAPI contract found.`,
            keyword: 'unregistered_route',
          },
        ],
      };
    }

    if (!contract.validator) {
      // Endpoint has no requestBody schema, passes validation
      const elapsed = performance.now() - startTime;
      return {
        valid: true,
        contract,
        validationLatencyMs: elapsed,
      };
    }

    const isValid = contract.validator(body);
    const elapsed = performance.now() - startTime;

    if (!isValid) {
      const errors = (contract.validator.errors || []).map(err => ({
        field: err.instancePath || err.schemaPath,
        message: err.message || 'Validation error',
        keyword: err.keyword,
        params: err.params,
      }));

      this.logger.warn(
        `Contract violation on [${method.toUpperCase()}] ${path}: ${JSON.stringify(errors)}`
      );

      return {
        valid: false,
        contract,
        errors,
        validationLatencyMs: elapsed,
      };
    }

    return {
      valid: true,
      contract,
      validationLatencyMs: elapsed,
    };
  }
}
