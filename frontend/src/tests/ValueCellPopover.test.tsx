import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ValueCellPopover, cellColorClass } from "@/components/ValueCellPopover";
import type { Cell } from "@/pages/companyDetailMocks";

const EXPLANATION =
  "Keine Quartalsschaetzung: FCF wird als operativer Cashflow minus "
  + "Investitionen berechnet; fuer dieses Quartal liegt keine OCF-/"
  + "Capex-Schaetzung vor. Der Jahreswert ist in der Annual-Spalte vorhanden.";

const notEstimatedCell: Cell = {
  value: null,
  is_forecast: true,
  primary_method: "not_estimated",
  source_name: EXPLANATION,
  value_key: "fcf",
  period_type: "Q4",
  period_year: 2026,
  period_label: "Q4 2026",
};

const anchorRect = {
  top: 10, bottom: 30, left: 10, right: 310, width: 300, height: 20,
  x: 10, y: 10, toJSON: () => ({}),
} as DOMRect;

describe("ValueCellPopover not_estimated", () => {
  it("zeigt Badge und Begruendung statt generischem Text", () => {
    render(
      <ValueCellPopover
        cell={notEstimatedCell}
        displayValue="—"
        onClose={vi.fn()}
        anchorRect={anchorRect}
      />,
    );

    expect(screen.getByText("Bewusst nicht geschaetzt")).toBeInTheDocument();
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();
    // Nicht der irrefuehrende rote not_found-Zustand.
    expect(screen.queryByText(/Nicht gefunden/)).not.toBeInTheDocument();
  });
});

describe("cellColorClass not_estimated", () => {
  it("rendert neutral grau wie not_yet_reported, nicht rot", () => {
    expect(cellColorClass(notEstimatedCell)).toBe("text-muted-foreground/60 italic");
    expect(cellColorClass(notEstimatedCell)).toBe(
      cellColorClass({ value: null, status: "not_yet_reported" } as Cell),
    );
  });

  it("bestehende Status unveraendert: not_found bleibt rot", () => {
    expect(
      cellColorClass({ value: null, primary_method: "not_found" } as Cell),
    ).toBe("text-red-600 font-semibold");
  });
});
