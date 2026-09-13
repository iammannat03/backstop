import postgres from "postgres";

// DATABASE_URL is a SQLAlchemy-style URL, strip the +driver suffix which
// postgres.js doesn't understand.
const rawUrl = process.env.DATABASE_URL;
if (!rawUrl) {
  throw new Error("DATABASE_URL not set");
}
const connectionString = rawUrl.replace("postgresql+psycopg2://", "postgresql://");

declare global {
  var __backstopSql: ReturnType<typeof postgres> | undefined;
}

// Reuse the connection across hot reloads in dev to avoid exhausting
// Postgres connections under `next dev`.
export const sql = global.__backstopSql ?? postgres(connectionString, { max: 5 });
if (process.env.NODE_ENV !== "production") {
  global.__backstopSql = sql;
}
