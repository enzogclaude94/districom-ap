"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

const loginSchema = z.object({
  email: z.string().trim().min(1, "Ingresá tu email."),
  password: z.string().min(1, "Ingresá tu contraseña."),
});

type LoginValues = z.infer<typeof loginSchema>;

/** Only allow same-site relative paths as post-login destinations (no open redirects). */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}

interface LoginFormProps {
  onSuccess: () => void;
}

export function LoginForm({ onSuccess }: LoginFormProps) {
  const queryClient = useQueryClient();
  const form = useForm<LoginValues>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });
  const login = useMutation({
    mutationFn: (values: LoginValues) => authApi.login(values.email, values.password),
    onSuccess: (user) => {
      queryClient.setQueryData(currentUserQueryKey, user);
      onSuccess();
    },
  });

  const { errors } = form.formState;
  const serverError =
    login.error instanceof ApiError ? login.error.message : login.error ? "No se pudo iniciar sesión." : null;

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit((values) => login.mutate(values))}>
      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="username"
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={errors.email ? "email-error" : undefined}
          {...form.register("email")}
        />
        {errors.email && (
          <p id="email-error" className="text-xs text-danger">
            {errors.email.message}
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="password">Contraseña</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={errors.password ? true : undefined}
          aria-describedby={errors.password ? "password-error" : undefined}
          {...form.register("password")}
        />
        {errors.password && (
          <p id="password-error" className="text-xs text-danger">
            {errors.password.message}
          </p>
        )}
      </div>
      {serverError && (
        <div role="alert" className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {serverError}
        </div>
      )}
      <Button type="submit" className="w-full" disabled={login.isPending}>
        {login.isPending ? "Ingresando…" : "Ingresar"}
      </Button>
    </form>
  );
}
