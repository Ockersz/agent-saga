import { Module } from '@nestjs/common';
import { ContractRegistryService } from './contracts/contract-registry.service';
import { ContractValidatorService } from './validator/contract-validator.service';
import { SagaJournalService } from './saga/saga-journal.service';
import { SagaCompensatorService } from './saga/saga-compensator.service';
import { GatewayProxyController } from './proxy/gateway-proxy.controller';

@Module({
  imports: [],
  controllers: [GatewayProxyController],
  providers: [
    ContractRegistryService,
    ContractValidatorService,
    SagaJournalService,
    SagaCompensatorService,
  ],
})
export class AppModule {}
