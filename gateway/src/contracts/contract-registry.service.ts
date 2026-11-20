import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'yaml';
import Ajv, { ValidateFunction } from 'ajv';
import addFormats from 'ajv-formats';

export interface CompensatingActionConfig {
  path: string;
  method: string;
  mapping: Record<string, string>;
}

export interface EndpointContract {
  serviceName: string;
  path: string;
  method: string;
  operationId?: string;
  actionType: 'compensatable' | 'pivot' | 'retriable';
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  compensatingAction?: CompensatingActionConfig;
  requestBodySchema?: any;
  validator?: ValidateFunction;
}

@Injectable()
export class ContractRegistryService implements OnModuleInit {
  private readonly logger = new Logger(ContractRegistryService.name);
  private readonly contracts = new Map<string, EndpointContract>();
  private readonly ajv: Ajv;

  constructor() {
    this.ajv = new Ajv({
      allErrors: true,
      strict: false,
      coerceTypes: true,
    });
    addFormats(this.ajv);
  }

  onModuleInit() {
    this.loadContracts();
  }

  public loadContracts(contractsDir?: string) {
    const dir = contractsDir || path.resolve(process.cwd(), '..', 'contracts');
    if (!fs.existsSync(dir)) {
      this.logger.warn(`Contracts directory not found at: ${dir}`);
      return;
    }

    const files = fs.readdirSync(dir).filter(f => f.endsWith('.yaml') || f.endsWith('.yml'));
    for (const file of files) {
      const fullPath = path.join(dir, file);
      try {
        const content = fs.readFileSync(fullPath, 'utf8');
        const spec = yaml.parse(content);
        this.parseOpenApiSpec(spec, file);
      } catch (err: any) {
        this.logger.error(`Failed to parse contract file: ${file}: ${err.message}`);
      }
    }

    this.logger.log(`Loaded ${this.contracts.size} endpoint contracts into memory.`);
  }

  private parseOpenApiSpec(spec: any, filename: string) {
    const serviceName = spec.info?.title || filename.replace(/\.ya?ml$/, '');
    const paths = spec.paths || {};

    for (const [routePath, methods] of Object.entries<any>(paths)) {
      for (const [method, op] of Object.entries<any>(methods)) {
        if (typeof op !== 'object' || !op) continue;

        const httpMethod = method.toUpperCase();
        const actionType = op['x-agentsaga-action-type'] || 'compensatable';
        const riskLevel = op['x-agentsaga-risk-level'] || 'medium';
        const compensatingAction = op['x-agentsaga-compensating-action'];

        const requestBodySchema =
          op.requestBody?.content?.['application/json']?.schema;

        let validator: ValidateFunction | undefined;
        if (requestBodySchema) {
          try {
            validator = this.ajv.compile(requestBodySchema);
          } catch (e: any) {
            this.logger.error(`Error compiling schema for ${httpMethod} ${routePath}: ${e.message}`);
          }
        }

        const contractKey = this.getContractKey(httpMethod, routePath);
        this.contracts.set(contractKey, {
          serviceName,
          path: routePath,
          method: httpMethod,
          operationId: op.operationId,
          actionType,
          riskLevel,
          compensatingAction,
          requestBodySchema,
          validator,
        });

        this.logger.debug(`Registered contract: [${httpMethod}] ${routePath} (Action: ${actionType})`);
      }
    }
  }

  public getContract(method: string, routePath: string): EndpointContract | undefined {
    // Exact match first
    const exactKey = this.getContractKey(method.toUpperCase(), routePath);
    if (this.contracts.has(exactKey)) {
      return this.contracts.get(exactKey);
    }

    // Pattern/parameter match: /api/v1/hr/employees/{employeeId}/status vs /api/v1/hr/employees/EMP-00104/status
    for (const [key, contract] of this.contracts.entries()) {
      if (contract.method !== method.toUpperCase()) continue;
      if (this.matchParameterizedPath(contract.path, routePath)) {
        return contract;
      }
    }

    return undefined;
  }

  public getAllContracts(): EndpointContract[] {
    return Array.from(this.contracts.values());
  }

  private getContractKey(method: string, routePath: string): string {
    return `${method.toUpperCase()}:${routePath}`;
  }

  private matchParameterizedPath(templatePath: string, actualPath: string): boolean {
    const templateParts = templatePath.split('/');
    const actualParts = actualPath.split('/');

    if (templateParts.length !== actualParts.length) return false;

    for (let i = 0; i < templateParts.length; i++) {
      const t = templateParts[i];
      const a = actualParts[i];
      if (t.startsWith('{') && t.endsWith('}')) {
        // Parameter segment matches anything non-empty
        if (!a) return false;
      } else if (t !== a) {
        return false;
      }
    }

    return true;
  }
}
