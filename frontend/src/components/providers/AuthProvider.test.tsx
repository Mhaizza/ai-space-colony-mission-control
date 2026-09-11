import { act } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { clearLocalAuthToken, setLocalAuthToken } from "@/auth/localAuth";
import { AuthProvider } from "./AuthProvider";

afterEach(() => {
  clearLocalAuthToken();
  vi.unstubAllEnvs();
});

it.each([false, true])(
  "hydrates local auth without a mismatch (stored token: %s)",
  async (signedIn) => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "local");
    clearLocalAuthToken();
    const content = (
      <AuthProvider>
        <main>Protected mission</main>
      </AuthProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    expect(container.textContent).not.toContain("Protected mission");
    // Browser storage becomes available after the server produced its HTML.
    if (signedIn) setLocalAuthToken("synthetic-hydration-test-token");
    const onRecoverableError = vi.fn();
    let root!: ReturnType<typeof hydrateRoot>;
    try {
      await act(async () => {
        root = hydrateRoot(container, content, { onRecoverableError });
      });
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container.textContent).toContain(
        signedIn ? "Protected mission" : "Continue",
      );
    } finally {
      await act(async () => root?.unmount());
    }
  },
);

it("preserves server rendering outside local mode when Clerk is disabled", () => {
  vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "clerk");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
  expect(
    renderToString(
      <AuthProvider>
        <main>Public shell</main>
      </AuthProvider>,
    ),
  ).toContain("Public shell");
});
