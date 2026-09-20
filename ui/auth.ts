import NextAuth from "next-auth";
import Slack from "next-auth/providers/slack";
import { resolveRole, roleForUser, SLACK_TEAM_CLAIM, type Role } from "@/lib/roles";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      name?: string | null;
      email?: string | null;
      image?: string | null;
      role: Role;
    };
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    slackTeamId?: string;
    role?: Role;
  }
}

function roleEnv() {
  return {
    AUTH_SLACK_TEAM_ID: process.env.AUTH_SLACK_TEAM_ID,
    AUTH_ADMIN_IDS: process.env.AUTH_ADMIN_IDS,
    AUTH_APPROVER_IDS: process.env.AUTH_APPROVER_IDS,
  };
}

// Reads AUTH_SECRET, AUTH_SLACK_ID and AUTH_SLACK_SECRET (and AUTH_URL when
// set) from the environment. Sessions are stateless JWTs, no database.
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Slack],
  session: { strategy: "jwt" },
  // Required behind the Amplify proxy, where the Host header is not the public URL.
  trustHost: true,
  callbacks: {
    signIn({ profile }) {
      const teamId = profile?.[SLACK_TEAM_CLAIM] as string | undefined;
      return resolveRole({ id: profile?.sub, teamId }, roleEnv()) !== null;
    },
    jwt({ token, profile }) {
      if (profile) {
        token.sub = profile.sub ?? token.sub;
        token.slackTeamId = profile[SLACK_TEAM_CLAIM] as string | undefined;
      }
      // Recomputed on every read so a change to the id lists applies without a new sign in.
      token.role = roleForUser(token.sub, roleEnv());
      return token;
    },
    session({ session, token }) {
      session.user.id = token.sub ?? "";
      session.user.role = (token.role as Role | undefined) ?? "viewer";
      return session;
    },
  },
});
