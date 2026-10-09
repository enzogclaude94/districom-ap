import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatMoney } from "@/lib/format";

import { paymentHref, type Payment } from "./api";
import { CONCEPT_LABELS, METHOD_LABELS, STATE_LABELS, STATE_TONES } from "./labels";

/** «Caja principal $ 100.000,00 · Banco $ 200.000,00»: where the money left from. */
export const meansSummary = (payment: Payment) =>
  payment.items
    .map((item) => `${item.account_name ?? METHOD_LABELS[item.method]} ${formatMoney(item.amount)}`)
    .join(" · ");

const conceptLine = (payment: Payment) =>
  payment.expense_category_name
    ? `${CONCEPT_LABELS[payment.concept]} · ${payment.expense_category_name}`
    : CONCEPT_LABELS[payment.concept];

/** Desktop table of payments (hidden on phones, where the cards below are shown instead). */
export function PaymentTable({ rows, withThirdParty = true }: { rows: Payment[]; withThirdParty?: boolean }) {
  return (
    <div className="hidden md:block">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fecha</TableHead>
            {withThirdParty && <TableHead>Tercero</TableHead>}
            <TableHead>Concepto</TableHead>
            <TableHead>Medios</TableHead>
            <TableHead className="text-right">Importe</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead>
              <span className="sr-only">Detalle</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((payment) => (
            <TableRow key={payment.id} data-testid={`payment-row-${payment.id}`}>
              <TableCell className="whitespace-nowrap tabular-nums">{formatDate(payment.business_date)}</TableCell>
              {withThirdParty && <TableCell className="max-w-56 break-words">{payment.third_party_name}</TableCell>}
              <TableCell className="max-w-56">
                <span className="block">{conceptLine(payment)}</span>
                {payment.reference && (
                  <span className="block truncate text-xs text-muted-foreground" title={payment.reference}>
                    Ref. {payment.reference}
                  </span>
                )}
              </TableCell>
              <TableCell className="max-w-64 text-xs text-muted-foreground">{meansSummary(payment)}</TableCell>
              <TableCell className="text-right whitespace-nowrap tabular-nums">{formatMoney(payment.amount)}</TableCell>
              <TableCell>
                <Badge tone={STATE_TONES[payment.state]}>{STATE_LABELS[payment.state]}</Badge>
              </TableCell>
              <TableCell>
                <Link
                  href={paymentHref(payment.id)}
                  className="whitespace-nowrap text-primary hover:underline"
                  aria-label={`Ver detalle del pago n.º ${payment.id}`}
                >
                  Ver detalle
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Phone cards of payments: no horizontal scroll. */
export function PaymentCards({ rows, withThirdParty = true }: { rows: Payment[]; withThirdParty?: boolean }) {
  return (
    <ul className="space-y-2 md:hidden" aria-label="Pagos">
      {rows.map((payment) => (
        <li key={payment.id}>
          <Card>
            <CardContent className="space-y-1 p-3 text-sm">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  {withThirdParty && <p className="font-medium break-words">{payment.third_party_name}</p>}
                  <p className="text-muted-foreground">
                    {formatDate(payment.business_date)} · {conceptLine(payment)}
                  </p>
                </div>
                <p className="font-medium whitespace-nowrap tabular-nums">{formatMoney(payment.amount)}</p>
              </div>
              <p className="text-xs break-words text-muted-foreground">{meansSummary(payment)}</p>
              <div className="flex items-center justify-between gap-2">
                <Badge tone={STATE_TONES[payment.state]}>{STATE_LABELS[payment.state]}</Badge>
                <Link
                  href={paymentHref(payment.id)}
                  className="text-primary hover:underline"
                  aria-label={`Ver detalle del pago n.º ${payment.id}`}
                >
                  Ver detalle
                </Link>
              </div>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}
