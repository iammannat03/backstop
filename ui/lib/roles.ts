// Pure role and workspace logic for Sign in with Slack. No framework imports
// so it can be unit-tested on its own.

export type Role = "admin" | "approver" | "viewer" | "guest";

const RANK: Record<Role, number> = { guest: 0, viewer: 1, approver: 2, admin: 3 };

export const SLACK_TEAM_CLAIM = "https://slack.com/team_id";

export interface RoleEnv {
  AUTH_SLACK_TEAM_ID?: string;
  AUTH_ADMIN_IDS?: string;
  AUTH_APPROVER_IDS?: string;
}

/** Splits a comma separated env value, trimming whitespace and dropping blanks. */
export function parseIdList(value: string | undefined | null): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * True when the Slack team id is acceptable. If AUTH_SLACK_TEAM_ID is not set
 * there is no workspace restriction, otherwise the ids must match exactly.
 */
export function isAllowedTeam(teamId: string | undefined | null, env: RoleEnv): boolean {
  const required = env.AUTH_SLACK_TEAM_ID?.trim();
  if (!required) return true;
  return typeof teamId === "string" && teamId.trim() === required;
}

/** Role for an allowed workspace member. Admin wins over approver. */
export function roleForUser(slackUserId: string | undefined | null, env: RoleEnv): Exclude<Role, "guest"> {
  const id = slackUserId?.trim();
  if (id) {
    if (parseIdList(env.AUTH_ADMIN_IDS).includes(id)) return "admin";
    if (parseIdList(env.AUTH_APPROVER_IDS).includes(id)) return "approver";
  }
  return "viewer";
}

/** Returns the role, or null when the user is not in the allowed workspace. */
export function resolveRole(
  user: { id?: string | null; teamId?: string | null },
  env: RoleEnv,
): Exclude<Role, "guest"> | null {
  if (!isAllowedTeam(user.teamId, env)) return null;
  return roleForUser(user.id, env);
}

export function hasRole(role: Role | undefined | null, min: Role): boolean {
  if (!role) return false;
  return RANK[role] >= RANK[min];
}
