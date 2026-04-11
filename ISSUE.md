# Known Issues

Minor issues encountered during extraction that are NOT bugs in this tool. Kept for reference so future agents don't re-investigate.

---

## Upstream: Missing image for `types-of-vpns` guide

- **Status**: Upstream CDN issue, no action required
- **Discovered**: 2026-04-10, during full guides batch extraction
- **Category**: software-architecture
- **Guide**: `types-of-vpns`
- **Broken URL**: `https://assets.bytebytego.com/diagrams/0404-vpns.png`
- **Response**: HTTP 404 from ByteByteGo CDN
- **Impact**: The MD and PDF files for this guide were still written successfully, but the image is missing. The MD file contains a reference to a local path that does not exist.
- **Resolution**: Leave as-is. This is a source-side problem (ByteByteGo's CDN), not a tool bug. If/when ByteByteGo re-uploads the image, re-running the extractor will pick it up automatically.
