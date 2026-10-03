"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Wordmark } from "@/components/brand/wordmark";
import { Card, CardContent } from "@/components/ui/card";
import { LoginForm, safeNextPath } from "@/features/auth/login-form";

function LoginCard() {
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));
  return (
    <div className="flex w-full max-w-sm flex-col items-center gap-8">
      <Wordmark height={40} priority />
      <Card className="w-full">
        <CardContent className="space-y-5 p-6">
          <div className="space-y-1">
            <h1 className="font-display text-lg font-semibold">Ingresar</h1>
            <p className="text-sm text-muted-foreground">Usá tu cuenta de DistriCom.</p>
          </div>
          <LoginForm onSuccess={() => router.replace(next)} />
        </CardContent>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <Suspense>
        <LoginCard />
      </Suspense>
    </main>
  );
}
