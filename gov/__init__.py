"""Seattle City Council meeting intelligence module.

Scrapes the list of meetings from seattlechannel.org, downloads SRT closed captions
and agenda PDFs, parses them into structured form, and delegates extraction to
Claude Code (via the /scout skill) for turning transcripts into weekly digests.
"""
