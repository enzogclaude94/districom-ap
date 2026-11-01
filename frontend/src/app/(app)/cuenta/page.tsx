import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { OwnSessions } from "@/features/auth/own-sessions";
import { PasswordForm } from "@/features/auth/password-form";

export const metadata: Metadata = { title: "Mi cuenta" };

export default function AccountPage() {
  return (
    <>
      <PageHeader title="Mi cuenta" description="Tu contraseña y las sesiones abiertas con tu usuario." />
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle>Contraseña</CardTitle>
        </CardHeader>
        <CardContent>
          <PasswordForm />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Sesiones abiertas</CardTitle>
          <CardDescription>Si no reconocés una sesión, cerrala y cambiá tu contraseña.</CardDescription>
        </CardHeader>
        <CardContent>
          <OwnSessions />
        </CardContent>
      </Card>
    </>
  );
}
