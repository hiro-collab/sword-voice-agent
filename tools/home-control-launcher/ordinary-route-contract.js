'use strict'

/**
 * 会話互換の枝: 通常会話routeがLauncherから参照する公開surface契約を読む。
 * Launcher lifecycleの内部authorityとは別で、公開pathと整合性だけを固定する。
 */

const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
const CONTRACT_PATH = path.join(
  PROJECT_ROOT,
  'contracts',
  'turn',
  'ordinary-standard-route.v1.json'
)
const SAFE_TOKEN = /^[a-z0-9_.-]{1,96}$/

const readContractBytes = () => fs.readFileSync(CONTRACT_PATH)

const loadContract = () => {
  let value
  try {
    value = JSON.parse(readContractBytes().toString('utf8'))
  } catch {
    throw new Error('ordinary_route_contract_unavailable')
  }
  validateContract(value)
  return value
}

const contractSha256 = () =>
  crypto.createHash('sha256').update(readContractBytes()).digest('hex')

const validateContract = (contract) => {
  if (!contract || contract.schema_version !== 'ordinary_standard_route.v1') {
    throw new Error('ordinary_route_contract_version_invalid')
  }
  const deadlines = contract.deadlines || {}
  if (
    !Number.isFinite(Number(deadlines.client_seconds)) ||
    !Number.isFinite(Number(deadlines.server_seconds)) ||
    Number(deadlines.client_seconds) <= 0 ||
    Number(deadlines.client_seconds) >= Number(deadlines.server_seconds) ||
    Number(deadlines.server_seconds) > 75
  ) {
    throw new Error('ordinary_route_contract_deadline_relation_invalid')
  }
  for (const flow of Object.values(contract.turn_accounting?.flows || {})) {
    if (
      !Number.isInteger(flow.user_turns) ||
      !Number.isInteger(flow.max_provider_requests) ||
      flow.abort_request_number !== flow.max_provider_requests + 1
    ) {
      throw new Error('ordinary_route_contract_flow_budget_invalid')
    }
  }
  if (contract.turn_accounting?.retry_count !== 0) {
    throw new Error('ordinary_route_contract_retry_invalid')
  }
  const reviewClasses = Object.values(contract.review_checkpoint?.classes || {})
  if (reviewClasses.length !== 10 || new Set(reviewClasses).size !== reviewClasses.length) {
    throw new Error('ordinary_route_contract_review_classes_invalid')
  }
  const result = contract.result_finalization || {}
  for (const key of ['phase_classes', 'finalization_classes', 'cleanup_classes']) {
    if (!Array.isArray(result[key]) || result[key].length === 0) {
      throw new Error('ordinary_route_contract_result_classes_invalid')
    }
  }
  return contract
}

const publicContractPayload = () => {
  const contract = loadContract()
  return {
    ok: true,
    contract_sha256: contractSha256(),
    contract,
    proof_ceiling: 'ordinary_standard_route_contract_only',
    raw_private_publication_flags: false
  }
}

const runtimeBinding = () => {
  const contract = loadContract()
  return {
    schema_version: 'ordinary_standard_route.binding.v1',
    contract_sha256: contractSha256(),
    profile_id: contract.profile_id,
    public_surfaces: contract.public_surfaces,
    readiness: contract.readiness,
    deadlines: contract.deadlines,
    turn_accounting: contract.turn_accounting,
    action_accounting: contract.action_accounting,
    review_checkpoint: contract.review_checkpoint,
    result_finalization: contract.result_finalization,
    raw_private_publication_flags: false
  }
}

const validateRuntimeBinding = (binding) => {
  const expected = runtimeBinding()
  if (stableJson(binding) !== stableJson(expected)) {
    throw new Error('ordinary_route_binding_drift')
  }
  return true
}

const assertLauncherRuntimeAlignment = ({
  profileId,
  publicSurfaces,
  expectedServiceIds
}) => {
  const contract = loadContract()
  if (profileId !== contract.profile_id) {
    throw new Error('ordinary_route_launcher_profile_drift')
  }
  if (stableJson(publicSurfaces) !== stableJson(contract.public_surfaces)) {
    throw new Error('ordinary_route_launcher_public_surface_drift')
  }
  if (stableJson(expectedServiceIds) !== stableJson(contract.readiness.expected_service_ids)) {
    throw new Error('ordinary_route_launcher_readiness_drift')
  }
  return true
}

const deriveBudget = (flowIds) => {
  const contract = loadContract()
  const flows = contract.turn_accounting.flows
  let userTurns = 0
  let maxProviderRequests = 0
  for (const flowId of flowIds) {
    const flow = flows[flowId]
    if (!flow) {
      throw new Error('ordinary_route_flow_unknown')
    }
    userTurns += flow.user_turns
    maxProviderRequests += flow.max_provider_requests
  }
  return {
    user_turns: userTurns,
    max_provider_requests: maxProviderRequests,
    abort_request_number: maxProviderRequests + 1,
    retry_count: contract.turn_accounting.retry_count
  }
}

const finalizeResult = (provisional, cleanupClass) => {
  const contract = loadContract()
  const resultContract = contract.result_finalization
  const allowedInput = new Set([
    ...resultContract.required_fields,
    ...resultContract.optional_fields
  ])
  const input = provisional && typeof provisional === 'object' && !Array.isArray(provisional)
    ? provisional
    : {}
  for (const key of Object.keys(input)) {
    if (!allowedInput.has(key)) {
      throw new Error('ordinary_route_result_field_rejected')
    }
  }
  if (
    input.schema_version !== undefined &&
    input.schema_version !== resultContract.schema_version
  ) {
    throw new Error('ordinary_route_result_version_drift')
  }
  if (
    input.contract_sha256 !== undefined &&
    input.contract_sha256 !== contractSha256()
  ) {
    throw new Error('ordinary_route_result_contract_drift')
  }
  if (!resultContract.cleanup_classes.includes(cleanupClass)) {
    throw new Error('ordinary_route_cleanup_class_invalid')
  }
  const safe = {
    schema_version: resultContract.schema_version,
    contract_sha256: contractSha256(),
    flow_id: safeToken(input.flow_id, 'unknown'),
    phase_class: allowedValue(
      input.phase_class,
      resultContract.phase_classes,
      'not_started'
    ),
    finalization_class: 'result_partial',
    cleanup_class: cleanupClass,
    user_turn_count: safeCount(input.user_turn_count),
    provider_request_count: safeCount(input.provider_request_count),
    bridge_submission_count: safeCount(input.bridge_submission_count),
    correlated_receipt_count: safeCount(input.correlated_receipt_count),
    success_presentation_count: safeCount(input.success_presentation_count),
    review_checkpoint_class: allowedValue(
      input.review_checkpoint_class,
      Object.values(contract.review_checkpoint.classes),
      contract.review_checkpoint.classes.not_checked
    )
  }
  for (const field of [
    'provider_request_certainty',
    'bridge_submission_certainty',
    'correlated_receipt_certainty',
    'success_presentation_certainty'
  ]) {
    if (input[field] !== undefined) {
      safe[field] = allowedValue(
        input[field],
        contract.action_accounting.certainty_classes,
        'unknown'
      )
    }
  }
  if (input.fixed_result_class !== undefined) {
    safe.fixed_result_class = safeToken(input.fixed_result_class, 'unknown')
  }
  if (safe.success_presentation_count > safe.correlated_receipt_count) {
    throw new Error('ordinary_route_receipt_order_invalid')
  }
  const hasBoundedPhase = safe.phase_class !== 'not_started'
  safe.finalization_class = !hasBoundedPhase
    ? 'result_missing'
    : safe.phase_class === 'stopped' && cleanupClass === 'cleanup_clear'
      ? 'result_complete'
      : 'result_partial'
  validateFinalResult(safe)
  return safe
}

const validateFinalResult = (value) => {
  const contract = loadContract()
  const resultContract = contract.result_finalization
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('ordinary_route_result_invalid')
  }
  const allowed = new Set([
    ...resultContract.required_fields,
    ...resultContract.optional_fields
  ])
  if (
    Object.keys(value).some((key) => !allowed.has(key)) ||
    resultContract.required_fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
  ) {
    throw new Error('ordinary_route_result_shape_invalid')
  }
  if (
    value.schema_version !== resultContract.schema_version ||
    value.contract_sha256 !== contractSha256() ||
    !resultContract.phase_classes.includes(value.phase_class) ||
    !resultContract.finalization_classes.includes(value.finalization_class) ||
    !resultContract.cleanup_classes.includes(value.cleanup_class) ||
    !Object.values(contract.review_checkpoint.classes).includes(value.review_checkpoint_class)
  ) {
    throw new Error('ordinary_route_result_drift')
  }
  for (const field of [
    'user_turn_count',
    'provider_request_count',
    'bridge_submission_count',
    'correlated_receipt_count',
    'success_presentation_count'
  ]) {
    if (!Number.isInteger(value[field]) || value[field] < 0 || value[field] > 64) {
      throw new Error('ordinary_route_result_counter_invalid')
    }
  }
  for (const field of [
    'provider_request_certainty',
    'bridge_submission_certainty',
    'correlated_receipt_certainty',
    'success_presentation_certainty'
  ]) {
    if (
      value[field] !== undefined &&
      !contract.action_accounting.certainty_classes.includes(value[field])
    ) {
      throw new Error('ordinary_route_result_certainty_invalid')
    }
  }
  if (
    value.fixed_result_class !== undefined &&
    !SAFE_TOKEN.test(String(value.fixed_result_class))
  ) {
    throw new Error('ordinary_route_result_class_invalid')
  }
  if (value.success_presentation_count > value.correlated_receipt_count) {
    throw new Error('ordinary_route_receipt_order_invalid')
  }
  return true
}

const stableJson = (value) => {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(',')}]`
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

const safeToken = (value, fallback) => {
  const text = String(value || '')
  return SAFE_TOKEN.test(text) ? text : fallback
}

const safeCount = (value) =>
  Number.isInteger(value) && value >= 0 && value <= 64 ? value : 0

const allowedValue = (value, allowed, fallback) =>
  allowed.includes(value) ? value : fallback

const readJsonObject = (filePath) => {
  const value = JSON.parse(fs.readFileSync(filePath, 'utf8'))
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('ordinary_route_json_object_required')
  }
  return value
}

const writeJsonAtomic = (filePath, value) => {
  const target = path.resolve(filePath)
  if (fs.existsSync(target)) {
    const existing = readJsonObject(target)
    if (stableJson(existing) === stableJson(value)) {
      return
    }
    throw new Error('ordinary_route_result_target_exists')
  }
  const partial = `${target}.partial`
  fs.writeFileSync(partial, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' })
  try {
    fs.renameSync(partial, target)
  } catch (error) {
    try { fs.unlinkSync(partial) } catch {}
    throw error
  }
}

const runCli = () => {
  const args = process.argv.slice(2)
  const command = args[0]
  if (command === '--emit-binding' && args.length === 1) {
    process.stdout.write(`${JSON.stringify(runtimeBinding())}\n`)
    return
  }
  if (command === '--validate-binding' && args.length === 2) {
    validateRuntimeBinding(readJsonObject(args[1]))
    process.stdout.write('ordinary_route_binding_valid\n')
    return
  }
  if (command === '--validate-result' && args.length === 2) {
    validateFinalResult(readJsonObject(args[1]))
    process.stdout.write('ordinary_route_result_valid\n')
    return
  }
  if (command === '--finalize-result' && args.length === 4) {
    const result = finalizeResult(readJsonObject(args[1]), args[3])
    writeJsonAtomic(args[2], result)
    process.stdout.write('ordinary_route_result_finalized\n')
    return
  }
  throw new Error('ordinary_route_contract_usage_invalid')
}

if (require.main === module) {
  try {
    runCli()
  } catch (error) {
    process.stderr.write(`${error.message || 'ordinary_route_contract_failed'}\n`)
    process.exitCode = 1
  }
}

module.exports = {
  CONTRACT_PATH,
  assertLauncherRuntimeAlignment,
  contractSha256,
  deriveBudget,
  finalizeResult,
  loadContract,
  publicContractPayload,
  runtimeBinding,
  validateContract,
  validateFinalResult,
  validateRuntimeBinding
}
