import { PageHeader } from "@/components/common/page-header";
import { MastersTabs } from "@/modules/commercial/masters/shared";

/** Comercial › Datos › Maestros (F1.7): one admin module with a subnav. Calidad diagnoses, Maestros corrects. */
export default function MastersLayout({ children }: LayoutProps<"/comercial/datos/maestros">) {
  return (
    <>
      <PageHeader
        title="Maestros"
        description="Lo que DistriCom sabe de clientes, productos, zonas, vendedores y configuración comercial. Los datos de origen vienen de STORE SYS y AXUM; acá se ven y se corrige lo que DistriCom decide."
      />
      <MastersTabs />
      <div className="space-y-4">{children}</div>
    </>
  );
}
