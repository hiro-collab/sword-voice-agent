#!/usr/bin/env node

import http from 'node:http'
import https from 'node:https'

const args = process.argv.slice(2)

const readArg = (name, fallback) => {
  const index = args.indexOf(name)
  if (index >= 0 && args[index + 1]) {
    return args[index + 1]
  }
  return fallback
}

const readIntArg = (name, fallback) => {
  const value = Number(readArg(name, fallback))
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : fallback
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const baseUrl = readArg('--base-url', 'http://127.0.0.1:8799').replace(/\/$/, '')
const intervalMs = readIntArg('--interval-ms', 1000)
const timeoutMs = readIntArg('--timeout-ms', 30000)

const getJson = (url) =>
  new Promise((resolve, reject) => {
    const client = url.startsWith('https:') ? https : http
    const request = client.get(url, { timeout: 2500 }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        body += chunk
      })
      response.on('end', () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`HTTP ${response.statusCode}: ${body.slice(0, 200)}`))
          return
        }
        try {
          resolve(JSON.parse(body))
        } catch (error) {
          reject(new Error(`invalid JSON from ${url}: ${error.message}`))
        }
      })
    })
    request.on('timeout', () => {
      request.destroy(new Error(`timeout reading ${url}`))
    })
    request.on('error', reject)
  })

const readSnapshot = async () => {
  const [readiness, startupTiming, diagnosticSurfaces] = await Promise.all([
    getJson(`${baseUrl}/api/demo-timed-action-readiness`),
    getJson(`${baseUrl}/api/startup-timing`),
    getJson(`${baseUrl}/api/diagnostic-surfaces`)
  ])
  return {
    schema_version: 'launcher_demo_timing_snapshot.v0',
    capturedAt: new Date().toISOString(),
    baseUrl,
    readiness,
    startupTiming: startupTiming.startupTiming || null,
    diagnosticSurfaces: diagnosticSurfaces.diagnosticSurfaces || null,
    proof_ceiling: 'launcher_demo_timing_snapshot_summary_only',
    command_submission_count: 0,
    raw_private_publication_flags: false,
    non_claims: [
      'not_runtime_success_by_itself',
      'not_first_audio_proof',
      'not_first_action_proof',
      'not_home_assistant_or_home_control_operation',
      'not_physical_device_proof'
    ]
  }
}

const main = async () => {
  const started = Date.now()
  let lastSnapshot = null
  let lastError = null

  while (Date.now() - started <= timeoutMs) {
    try {
      lastSnapshot = await readSnapshot()
      lastError = null
      if (lastSnapshot.readiness?.readiness_class === 'ready_for_reviewed_first_action_handoff') {
        console.log(JSON.stringify(lastSnapshot, null, 2))
        return
      }
    } catch (error) {
      lastError = error
    }
    await sleep(intervalMs)
  }

  if (lastSnapshot) {
    console.log(JSON.stringify({
      ...lastSnapshot,
      collection_status_class: 'timed_out_waiting_for_first_action_readiness'
    }, null, 2))
    process.exitCode = 2
    return
  }

  console.log(JSON.stringify({
    schema_version: 'launcher_demo_timing_snapshot.v0',
    capturedAt: new Date().toISOString(),
    baseUrl,
    collection_status_class: 'blocked_launcher_summary_unavailable',
    error_class: String(lastError?.message || 'unknown').slice(0, 200),
    command_submission_count: 0,
    raw_private_publication_flags: false
  }, null, 2))
  process.exitCode = 1
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
