import "server-only";
import { headers } from "next/headers";
import { getToken } from "next-auth/jwt";
import { auth as publicAuth } from "@/auth";

/** Attach the provider token exclusively inside the Next.js server. */
export async function auth() {
  const session = await publicAuth();
  if (!session) return null;
  const token = await getToken({
    req: { headers: new Headers(await headers()) },
    secret: process.env.AUTH_SECRET,
    secureCookie: (process.env.AUTH_URL ?? "http://localhost:3000").startsWith(
      "https://",
    ),
  });
  if (!token?.accessToken) return null;
  return { ...session, accessToken: token.accessToken as string };
}
