import { NestFactory } from '@nestjs/core';
import { Logger } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const logger = new Logger('AgentSagaGateway');
  const app = await NestFactory.create(AppModule);

  // Enable CORS and raw/json parsing
  app.enableCors();

  const port = process.env.PORT || 8080;
  await app.listen(port);
  logger.log(`🚀 AgentSaga-Gateway is listening on port ${port}`);
  logger.log(`🛡️  Zero-Trust OpenAPI Validation & SAGA Rollback Engine Active`);
}

bootstrap();
