"use client";

import { LogOut } from "lucide-react";

import { Wordmark } from "@/components/brand/wordmark";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { PasswordForm } from "./password-form";

interface PasswordChangeRequiredProps {
  fullName: string;
  onLogout: () => void;
  loggingOut: boolean;
}

/**
 * First login with a temporary password (S2): nothing of the app is shown until it is replaced. The backend
 * enforces it too: it answers 403 `password_change_required` to everything but the change itself.
 */
export function PasswordChangeRequired({ fullName, onLogout, loggingOut }: PasswordChangeRequiredProps) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-8">
        <Wordmark height={40} priority />
        <Card className="w-full">
          <CardContent className="space-y-5 p-6">
            <div className="space-y-1">
              <h1 className="font-display text-lg font-semibold">Elegí tu contraseña</h1>
              <p className="text-sm text-muted-foreground">
                Hola, {fullName}. Ingresaste con una contraseña temporal: para continuar, reemplazala por una propia que
                solo vos conozcas.
              </p>
            </div>
            <PasswordForm temporary />
          </CardContent>
        </Card>
        <Button variant="ghost" size="sm" disabled={loggingOut} onClick={onLogout}>
          <LogOut aria-hidden />
          Cerrar sesión
        </Button>
      </div>
    </main>
  );
}
