import { Module } from '@nestjs/common';
import { ContractRegistryService } from './contracts/contract-registry.service';
import { ContractValidatorService } from './validator/contract-validator.service';
import { PolicyEvaluatorService } from './policy/policy-evaluator.service';
import { SagaJournalService } from './saga/saga-journal.service';
import { SagaCompensatorService } from './saga/saga-compensator.service';
import { GatewayProxyController } from './proxy/gateway-proxy.controller';
import { HealthController } from './health.controller';

@Module({
  imports: [],
  controllers: [HealthController, GatewayProxyController],
  providers: [
    ContractRegistryService,
    ContractValidatorService,
    PolicyEvaluatorService,
    SagaJournalService,
    SagaCompensatorService,
  ],
})
export class AppModule {}
