import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { resolve } from 'path';
import { execSync } from 'child_process';

const SPEC_PATH = resolve('../../docs/api-reference.json');
const OUTPUT_DIR = resolve('./src/generated');
const OUTPUT_FILE = resolve('./src/generated/api.ts');

if (!existsSync(SPEC_PATH)) {
  console.error(`Spec file not found: ${SPEC_PATH}`);
  process.exit(1);
}

mkdirSync(OUTPUT_DIR, { recursive: true });

console.log('Generating API client from OpenAPI spec...');

try {
  execSync(`npx openapi-typescript ${SPEC_PATH} --output ${OUTPUT_FILE}`, {
    stdio: 'inherit',
    cwd: resolve('./'),
  });
  console.log('✅ API client generated successfully');
} catch (e) {
  console.error('❌ Generation failed:', e.message);
  process.exit(1);
}