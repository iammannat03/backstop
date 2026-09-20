import { NextResponse } from "next/server";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { hasRole, type Role } from "@/lib/roles";

export interface Viewer {
  id: string;
  name: string | null;
  email: string | null;
  image: string | null;
  role: Role;
}

const GUEST: Viewer = { id: "guest", name: "Guest", email: null, image: null, role: "guest" };

export function guestModeEnabled(): boolean {
  return process.env.AUTH_ALLOW_GUEST === "true";
}

/** Signed-in viewer, or a read-only guest when guest mode is on, else null. */
export async function getViewer(): Promise<Viewer | null> {
  const session = await auth();
  const user = session?.user;
  if (user?.id) {
    return {
      id: user.id,
      name: user.name ?? null,
      email: user.email ?? null,
      image: user.image ?? null,
      role: user.role,
    };
  }
  return guestModeEnabled() ? GUEST : null;
}

/** Page guard: any signed-in user (or guest when allowed), else redirect to sign in. */
export async function requirePageViewer(callbackUrl = "/"): Promise<Viewer> {
  const viewer = await getViewer();
  if (!viewer) redirect(`/api/auth/signin?callbackUrl=${encodeURIComponent(callbackUrl)}`);
  return viewer;
}

type GuardResult = { viewer: Viewer; response?: undefined } | { viewer?: undefined; response: NextResponse };

/**
 * Route handler guard. Reads accept a guest when guest mode is on, anything
 * above "viewer" never does. 401 when unauthenticated, 403 when the role is too low.
 */
export async function guardApi(min: Exclude<Role, "guest">): Promise<GuardResult> {
  const viewer = await getViewer();
  if (!viewer) {
    return { response: NextResponse.json({ error: "unauthenticated" }, { status: 401 }) };
  }
  // A guest is unauthenticated, so anything beyond a read is a 401, not a 403.
  if (viewer.role === "guest" && min !== "viewer") {
    return { response: NextResponse.json({ error: "unauthenticated" }, { status: 401 }) };
  }
  const allowed = min === "viewer" ? hasRole(viewer.role, "guest") : hasRole(viewer.role, min);
  if (!allowed) {
    return { response: NextResponse.json({ error: "forbidden" }, { status: 403 }) };
  }
  return { viewer };
}
