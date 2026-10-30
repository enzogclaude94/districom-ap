import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { NewUserButton, UserList } from "@/features/users/user-list";

export const metadata: Metadata = { title: "Usuarios" };

export default function UsersPage() {
  return (
    <>
      <PageHeader
        title="Usuarios"
        description="Cuentas para ingresar a DistriCom, sus accesos por módulo y sus sesiones."
        actions={<NewUserButton />}
      />
      <UserList />
    </>
  );
}
