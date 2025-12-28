from __future__ import annotations

from collections import OrderedDict

from apps.api.app.schemas import CitationGroupOut, CitationOut


def build_citation_groups(citations: list[CitationOut]) -> list[CitationGroupOut]:
    groups: "OrderedDict[str, CitationGroupOut]" = OrderedDict()
    for citation in citations:
        source_key = str(citation.source_id)
        group = groups.get(source_key)
        if group is None:
            group = CitationGroupOut(
                source_id=citation.source_id,
                source_title=citation.source_title,
                citations=[],
            )
            groups[source_key] = group
        group.citations.append(citation)
    return list(groups.values())
