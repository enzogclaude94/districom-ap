"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authApi, currentUserQueryKey, ownSessionsQueryKey } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

/** Same minimum as the backend (`MIN_PASSWORD_LENGTH`); the backend checks the rest and has the last word. */
export const MIN_PASSWORD_LENGTH = 12;

const passwordSchema = z
  .object({
    current: z.string().min(1, "Ingresá tu contraseña actual."),
    next: z.string().min(MIN_PASSWORD_LENGTH, `Usá al menos ${MIN_PASSWORD_LENGTH} caracteres.`),
    confirm: z.string(),
  })
  .refine((values) => values.next === values.confirm, {
    path: ["confirm"],
    message: "Las contraseñas no coinciden.",
  });

type PasswordValues = z.infer<typeof passwordSchema>;

interface PasswordFormProps {
  /** A temporary password is being replaced: the labels say so. */
  temporary?: boolean;
}

/** Change one's own password. The backend closes every other session of the user; this one stays. */
export function PasswordForm({ temporary = false }: PasswordFormProps) {
  const queryClient = useQueryClient();
  const form = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { current: "", next: "", confirm: "" },
  });
  const change = useMutation({
    mutationFn: (values: PasswordValues) => authApi.changePassword(values.current, values.next),
    onSuccess: (user) => {
      form.reset();
      queryClient.setQueryData(currentUserQueryKey, user);
      void queryClient.invalidateQueries({ queryKey: ownSessionsQueryKey });
    },
  });

  const { errors } = form.formState;
  const serverError =
    change.error instanceof ApiError ? change.error.message : change.error ? "No se pudo cambiar la contraseña." : null;
  const fields = [
    {
      name: "current",
      label: temporary ? "Contraseña temporal" : "Contraseña actual",
      autoComplete: "current-password",
    },
    { name: "next", label: "Contraseña nueva", autoComplete: "new-password" },
    { name: "confirm", label: "Repetí la contraseña nueva", autoComplete: "new-password" },
  ] as const;

  return (
    <form noValidate className="space-y-4" onSubmit={form.handleSubmit((values) => change.mutate(values))}>
      {fields.map((field) => (
        <div key={field.name} className="space-y-1.5">
          <Label htmlFor={`password-${field.name}`}>{field.label}</Label>
          <Input
            id={`password-${field.name}`}
            type="password"
            autoComplete={field.autoComplete}
            aria-invalid={errors[field.name] ? true : undefined}
            aria-describedby={errors[field.name] ? `password-${field.name}-error` : undefined}
            {...form.register(field.name)}
          />
          {errors[field.name] && (
            <p id={`password-${field.name}-error`} className="text-xs text-danger">
              {errors[field.name]?.message}
            </p>
          )}
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        Al menos {MIN_PASSWORD_LENGTH} caracteres. Una frase que recuerdes es mejor que una palabra con símbolos. Al
        cambiarla se cierran tus otras sesiones abiertas.
      </p>
      {serverError && (
        <div role="alert" className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {serverError}
        </div>
      )}
      {change.isSuccess && !temporary && (
        <div role="status" className="flex items-start gap-2 rounded-md bg-success-soft px-3 py-2 text-sm text-success">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
          Contraseña cambiada. Tus otras sesiones se cerraron.
        </div>
      )}
      <Button type="submit" disabled={change.isPending}>
        {change.isPending ? "Guardando…" : "Cambiar contraseña"}
      </Button>
    </form>
  );
}
