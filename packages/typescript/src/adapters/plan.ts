/**
 * Planned platform I/O. Adapters describe calls; Layers/transports dispatch them.
 */
export type HttpJsonCall = {
  readonly transport: "http_json"
  readonly method: string
  readonly url: string
  readonly json?: { readonly [key: string]: unknown }
  readonly headers?: { readonly [key: string]: string }
  readonly native: string
}

export type HttpFormCall = {
  readonly transport: "http_form"
  readonly method: string
  readonly url: string
  readonly form: { readonly [key: string]: string }
  readonly headers?: { readonly [key: string]: string }
  readonly native: string
}

export type SmtpCall = {
  readonly transport: "smtp"
  readonly native: string
  readonly email: {
    readonly from: string
    readonly to: string
    readonly subject: string
    readonly body: string
    readonly in_reply_to: string
    readonly references: string
    readonly attachments: ReadonlyArray<{
      readonly filename: string
      readonly url: string
      readonly mime_type: string
    }>
  }
}

export type TwimlCall = {
  readonly transport: "twiml"
  readonly native: string
  readonly twiml: string
}

export type PlannedCall = HttpJsonCall | HttpFormCall | SmtpCall | TwimlCall
