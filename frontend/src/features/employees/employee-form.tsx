"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import type { Employee, EmployeeInput } from "@/lib/api/employees";

const schema = z.object({
  full_name: z.string().trim().min(1, "Ingresá el nombre.").max(200, "Máximo 200 caracteres."),
  hire_date: z.string(),
  phone: z.string().trim().max(50, "Máximo 50 caracteres."),
});

type Values = z.infer<typeof schema>;

interface EmployeeFormProps {
  employee?: Employee;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (input: EmployeeInput) => void;
  onCancel?: () => void;
}

/** Identity data of an employee. External system ids are linked by imports, not typed here. */
export function EmployeeForm({ employee, submitLabel, pending, error, onSubmit, onCancel }: EmployeeFormProps) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      full_name: employee?.full_name ?? "",
      hire_date: employee?.hire_date ?? "",
      phone: employee?.phone ?? "",
    },
  });
  const { errors } = form.formState;
  const serverError = error instanceof ApiError ? error.message : error ? "No se pudo guardar." : null;

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={form.handleSubmit((values) =>
        onSubmit({ full_name: values.full_name, hire_date: values.hire_date || null, phone: values.phone || null }),
      )}
    >
      <div className="space-y-1.5">
        <Label htmlFor="full_name">Nombre y apellido</Label>
        <Input
          id="full_name"
          autoComplete="off"
          aria-invalid={errors.full_name ? true : undefined}
          aria-describedby={errors.full_name ? "full_name-error" : undefined}
          {...form.register("full_name")}
        />
        {errors.full_name && (
          <p id="full_name-error" className="text-xs text-danger">
            {errors.full_name.message}
          </p>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="hire_date">Fecha de ingreso</Label>
          <Input id="hire_date" type="date" {...form.register("hire_date")} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="phone">Teléfono</Label>
          <Input
            id="phone"
            type="tel"
            autoComplete="off"
            aria-invalid={errors.phone ? true : undefined}
            {...form.register("phone")}
          />
          {errors.phone && <p className="text-xs text-danger">{errors.phone.message}</p>}
        </div>
      </div>
      {serverError && (
        <div role="alert" className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {serverError}
        </div>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? "Guardando…" : submitLabel}
        </Button>
        {onCancel && (
          <Button variant="secondary" onClick={onCancel}>
            Cancelar
          </Button>
        )}
      </div>
    </form>
  );
}
