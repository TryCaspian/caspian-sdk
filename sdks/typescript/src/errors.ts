/** Raised when the gateway returns a non-2xx response (or a transport error). */
export class CommError extends Error {
  readonly statusCode: number;
  readonly detail: string;

  constructor(statusCode: number, detail: string) {
    super(`${statusCode}: ${detail}`);
    this.name = "CommError";
    this.statusCode = statusCode;
    this.detail = detail;
    // Restore the prototype chain (transpilation to ES5 loses it).
    Object.setPrototypeOf(this, CommError.prototype);
  }
}
