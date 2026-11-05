import { describe, expect, it } from "vitest";
import { ApiError, formatDate } from "./api";

describe("client contracts", () => {
  it("preserves machine-readable error codes", () => {
    const error = new ApiError("INSUFFICIENT_STOCK", "Stock changed", 409);
    expect(error.code).toBe("INSUFFICIENT_STOCK");
    expect(error.status).toBe(409);
  });
  it("renders no-expiry lots without inventing a date", () => {
    expect(formatDate(null)).toBe("No expiry");
  });
});
