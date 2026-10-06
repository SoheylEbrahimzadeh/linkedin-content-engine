// LCE-032: the Worker can apply its own D1 migrations through an Access-protected,
// typed-phrase API call, for deployments where `wrangler d1 migrations apply` has
// not run (Workers Builds applies none). Same files as cloud/migrations, recorded
// in Wrangler's own `d1_migrations` table, so Wrangler later sees them as applied.

import m0001 from "../migrations/0001_init.sql";
import m0002 from "../migrations/0002_images.sql";
import m0003 from "../migrations/0003_pipeline.sql";
import m0004 from "../migrations/0004_decisions.sql";
import m0005 from "../migrations/0005_preview_media.sql";
import m0006 from "../migrations/0006_freshness.sql";
import m0007 from "../migrations/0007_refresh.sql";
import m0008 from "../migrations/0008_reliability.sql";
import m0009 from "../migrations/0009_refresh_progress.sql";
import m0010 from "../migrations/0010_archive.sql";

export const MIGRATIONS: [string, string][] = [
  ["0001_init.sql", m0001],
  ["0002_images.sql", m0002],
  ["0003_pipeline.sql", m0003],
  ["0004_decisions.sql", m0004],
  ["0005_preview_media.sql", m0005],
  ["0006_freshness.sql", m0006],
  ["0007_refresh.sql", m0007],
  ["0008_reliability.sql", m0008],
  ["0009_refresh_progress.sql", m0009],
  ["0010_archive.sql", m0010],
];

// Exactly Wrangler's table (getCreateMigrationsTableQuery, default name d1_migrations).
const CREATE_LOG = `CREATE TABLE IF NOT EXISTS d1_migrations(
		id         INTEGER PRIMARY KEY AUTOINCREMENT,
		name       TEXT UNIQUE,
		applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);`;

/** Statements of a migration file: full-line `--` comments dropped, split on `;`. */
export function statements(sql: string): string[] {
  const body = sql.split("\n").filter((l) => !l.trim().startsWith("--")).join("\n");
  return body.split(";").map((s) => s.trim()).filter(Boolean);
}

async function tables(db: D1Database): Promise<Set<string>> {
  const rows = (await db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all<{ name: string }>()).results;
  return new Set(rows.map((r) => r.name));
}

export async function migrationStatus(db: D1Database): Promise<{ applied: string[]; pending: string[] }> {
  const have = await tables(db);
  const applied = have.has("d1_migrations")
    ? (await db.prepare("SELECT name FROM d1_migrations ORDER BY id").all<{ name: string }>()).results.map((r) => r.name)
    : [];
  return { applied, pending: MIGRATIONS.map(([n]) => n).filter((n) => !applied.includes(n)) };
}

export class MigrationConflict extends Error {}

/** Applies pending migrations in order; each file and its log row in one D1 batch (atomic). */
export async function applyMigrations(db: D1Database): Promise<string[]> {
  const { pending } = await migrationStatus(db);
  const done: string[] = [];
  if (pending.length === 0) return done;
  await db.prepare(CREATE_LOG).run();
  for (const [name, sql] of MIGRATIONS) {
    if (!pending.includes(name)) continue;
    const stmts = statements(sql);
    const have = await tables(db);
    for (const s of stmts) {
      const create = /^CREATE TABLE\s+(\w+)/i.exec(s);
      if (create && have.has(create[1])) {
        throw new MigrationConflict(`${name}: table ${create[1]} exists but the migration is not recorded; resolve manually`);
      }
      const alter = /^ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)/i.exec(s);
      if (alter && have.has(alter[1])) {
        const cols = (await db.prepare(`PRAGMA table_info(${alter[1]})`).all<{ name: string }>()).results;
        if (cols.some((c) => c.name === alter[2])) {
          throw new MigrationConflict(`${name}: column ${alter[1]}.${alter[2]} exists but the migration is not recorded`);
        }
      }
    }
    await db.batch([...stmts.map((s) => db.prepare(s)),
      db.prepare("INSERT INTO d1_migrations (name) VALUES (?)").bind(name)]);
    done.push(name);
  }
  return done;
}
