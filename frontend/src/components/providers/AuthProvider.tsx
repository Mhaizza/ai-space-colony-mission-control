"use client";

import { ClerkProvider } from "@clerk/nextjs";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { isLikelyValidClerkPublishableKey } from "@/auth/clerkKey";
import {
  clearLocalAuthToken,
  getLocalAuthToken,
  isLocalAuthMode,
} from "@/auth/localAuth";
import { LocalAuthLogin } from "@/components/organisms/LocalAuthLogin";

const subscribeToHydration = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

export function AuthProvider({ children }: { children: ReactNode }) {
  const localMode = isLocalAuthMode();
  const hydrated = useSyncExternalStore(
    subscribeToHydration,
    clientSnapshot,
    serverSnapshot,
  );

  useEffect(() => {
    if (!localMode) {
      clearLocalAuthToken();
    }
  }, [localMode]);

  if (localMode) {
    // Browser storage must not choose a different tree on the first hydration render.
    if (!hydrated) {
      return (
        <div
          role="status"
          className="flex min-h-screen items-center justify-center"
        >
          Loading…
        </div>
      );
    }
    if (!getLocalAuthToken()) {
      return <LocalAuthLogin />;
    }
    return <>{children}</>;
  }

  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const afterSignOutUrl =
    process.env.NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL ?? "/";

  if (!isLikelyValidClerkPublishableKey(publishableKey)) {
    return <>{children}</>;
  }

  return (
    <ClerkProvider
      publishableKey={publishableKey}
      afterSignOutUrl={afterSignOutUrl}
    >
      {children}
    </ClerkProvider>
  );
}
