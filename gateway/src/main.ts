import { NestFactory } from '@nestjs/core';
import { Logger } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const quiet = process.env.BENCHMARK_QUIET === '1';
  const logger = new Logger('AgentSagaGateway');
  const app = await NestFactory.create(AppModule, {
    logger: quiet ? false : undefined,
  });

  app.enableCors();

  const port = process.env.PORT || 8080;
  await app.listen(port);
  if (!quiet) {
    logger.log(`AgentSaga-Gateway is listening on port ${port}`);
    logger.log(`Zero-Trust OpenAPI Validation, Policy Gate & SAGA Rollback Engine Active`);
  }
}

bootstrap();
