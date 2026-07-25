from pathlib import Path

import yaml
from pydantic import BaseModel


class SafeUniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: SafeUniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            mark = key_node.start_mark
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate mapping key {key!r}",
                mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


SafeUniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_yaml[T: BaseModel](path: Path | str, model_type: type[T]) -> T:
    config_path = Path(path)
    document = yaml.load(
        config_path.read_text(encoding="utf-8"),
        Loader=SafeUniqueKeyLoader,
    )
    if not isinstance(document, dict):
        raise TypeError(
            f"YAML document at {config_path} must be a top-level mapping, "
            f"got {type(document).__name__}"
        )
    return model_type.model_validate(document)
