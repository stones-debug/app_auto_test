// Step 11：构建体积预算脚本——扫描 dist/assets，超限退出 1。
// 预算：单 JS gzip ≤ 200 kB，单 CSS gzip ≤ 80 kB。
import { deflateSync, gzipSync } from 'node:zlib'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const distAssets = fileURLToPath(new URL('../dist/assets', import.meta.url))

let files
try {
  files = readdirSync(distAssets)
} catch {
  console.error('dist/assets 不存在，请先执行 npm run build')
  process.exit(1)
}

const budget = { '.js': 200 * 1024, '.css': 80 * 1024 }
const failures = []
const summary = []

for (const name of files) {
  const raw = readFileSync(join(distAssets, name))
  const gz = gzipSync(raw, { level: 9 }).length
  const ext = name.slice(name.lastIndexOf('.'))
  const limit = budget[ext]
  if (limit === undefined) continue
  summary.push({ name, raw: raw.length, gz, limit, ok: gz <= limit })
  if (gz > limit) {
    failures.push(`${name}: gzip ${(gz / 1024).toFixed(1)} kB > ${(limit / 1024).toFixed(0)} kB 上限`)
  }
}

// CI 可读摘要（不提交 hash 文件名产物）
for (const s of summary.sort((a, b) => b.raw - a.raw)) {
  console.log(
    `${s.ok ? 'ok  ' : 'OVER'} ${s.name}  raw=${(s.raw / 1024).toFixed(1)} kB  gzip=${(s.gz / 1024).toFixed(1)} kB  (limit ${(s.limit / 1024).toFixed(0)} kB)`,
  )
}

if (failures.length) {
  console.error('\nBundle 体积超限：')
  for (const f of failures) console.error('  ' + f)
  process.exit(1)
}
console.log('\nBundle 预算检查通过（JS gzip ≤ 200 kB，CSS gzip ≤ 80 kB）')