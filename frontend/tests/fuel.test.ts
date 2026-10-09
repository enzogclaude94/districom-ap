import { describe, expect, it } from "vitest";

import { estimateAmount, formatQuantity, parseDecimal, parseOdometer } from "@/modules/settlements/delivery/fuel";

describe("fuel quantities", () => {
  it("reads liters and prices typed the Argentine way", () => {
    expect(parseDecimal("45,678", 3)).toBe("45.678");
    expect(parseDecimal("1.234,5678", 4)).toBe("1234.5678");
    expect(parseDecimal("1.500", 3)).toBe("1500.000");
    expect(parseDecimal("1,0001", 3)).toBeNull();
    expect(parseDecimal("0", 3)).toBeNull();
    expect(parseDecimal("abc", 3)).toBeNull();
  });

  it("estimates the amount half up, exactly like the backend", () => {
    expect(estimateAmount("0.125", "1.0000")).toBe("0.13");
    expect(estimateAmount("45.678", "1234.5678")).toBe("56392.59");
    expect(estimateAmount("33.333", "1499.9900")).toBe("49999.17");
    expect(estimateAmount("10.005", "1000.0000")).toBe("10005.00");
    expect(estimateAmount("100.000", "9999999999.9999")).toBe("999999999999.99");
  });

  it("prints quantities and reads the odometer", () => {
    expect(formatQuantity("45.678")).toBe("45,678");
    expect(formatQuantity("10.000")).toBe("10");
    expect(formatQuantity("1234.5000")).toBe("1.234,5");
    expect(parseOdometer("")).toBeNull();
    expect(parseOdometer("120.500")).toBe(120500);
    expect(parseOdometer("-1")).toBeUndefined();
  });
});
