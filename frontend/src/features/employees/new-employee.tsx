"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { Card, CardContent } from "@/components/ui/card";
import { employeesApi, employeesQueryKey } from "@/lib/api/employees";

import { EmployeeForm } from "./employee-form";

export function NewEmployee() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: employeesApi.create,
    onSuccess: (employee) => {
      queryClient.setQueryData([...employeesQueryKey, employee.id], employee);
      void queryClient.invalidateQueries({ queryKey: [...employeesQueryKey, "list"] });
      router.push(`/empleados/${employee.id}`);
    },
  });
  return (
    <Card className="max-w-2xl">
      <CardContent className="pt-6">
        <EmployeeForm
          submitLabel="Crear empleado"
          pending={create.isPending || create.isSuccess}
          error={create.error}
          onSubmit={(input) => create.mutate(input)}
          onCancel={() => router.push("/empleados")}
        />
      </CardContent>
    </Card>
  );
}
