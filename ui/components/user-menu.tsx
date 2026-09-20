import { signIn, signOut } from "@/auth";
import { getViewer } from "@/lib/authz";

const ROLE_STYLE: Record<string, string> = {
  admin: "border-accent text-accent",
  approver: "border-neutral-700 text-neutral-800",
  viewer: "border-neutral-400 text-neutral-600",
  guest: "border-neutral-400 text-neutral-600",
};

async function signInAction() {
  "use server";
  await signIn("slack", { redirectTo: "/" });
}

async function signOutAction() {
  "use server";
  await signOut({ redirectTo: "/" });
}

export async function UserMenu() {
  const viewer = await getViewer();
  const signedIn = viewer && viewer.role !== "guest";

  return (
    <div className="flex items-center gap-2.5 border-l border-divider pl-4">
      {viewer && (
        <>
          {viewer.image && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={viewer.image} alt="" width={20} height={20} className="size-5 shrink-0" />
          )}
          <span className="font-data hidden max-w-[140px] truncate text-[11px] text-text sm:inline">
            {viewer.name ?? viewer.email ?? viewer.id}
          </span>
          <span
            className={`font-data border px-1.5 py-px text-[9.5px] tracking-[0.1em] uppercase ${ROLE_STYLE[viewer.role] ?? ROLE_STYLE.viewer}`}
          >
            {viewer.role}
          </span>
        </>
      )}
      <form action={signedIn ? signOutAction : signInAction}>
        <button type="submit" className="font-data text-[11px] tracking-[0.06em] text-neutral-700 underline-offset-2 hover:text-text hover:underline">
          {signedIn ? "Sign out" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
