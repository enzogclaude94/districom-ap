"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { LoginForm, safeNextPath } from "@/features/auth/login-form";

function LoginCard() {
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));
  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">Districom</CardTitle>
        <CardDescription>Ingresá para ver la inteligencia comercial.</CardDescription>
      </CardHeader>
      <CardContent>
        <LoginForm onSuccess={() => router.replace(next)} />
      </CardContent>
    </Card>
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
