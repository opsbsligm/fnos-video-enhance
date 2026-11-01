const fs = require('node:fs');
const Ajv = require('ajv');
// 与实例 gitlab-security_report_schemas 15.2.4 完全相同的官方 schema。
const schema = require('./schemas/dependency-scanning-15.2.4.json');
const validate = new Ajv({strict: false, allErrors: true}).compile(schema);
module.exports = validate;
if (require.main === module) {
  const report = JSON.parse(fs.readFileSync('gl-dependency-scanning-report.json', 'utf8'));
  if (!validate(report)) {
    console.error('依赖报告不符合 GitLab schema', validate.errors);
    process.exit(1);
  }
  console.log('GitLab dependency_scanning schema 15.2.4 校验通过');
}
