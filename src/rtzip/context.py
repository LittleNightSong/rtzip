import dataclasses


def _merge(ctx: 'Context', other: 'Context') -> 'Context':
    dct = {}

    for f in dataclasses.fields(ctx):
        if f.name == 'extras':
            continue

        x = getattr(ctx, f.name)
        if f.default is not dataclasses.MISSING and f.default is x:
            continue

        if f.default_factory is not dataclasses.MISSING and not bool(x):
            continue

        dct[f.name] = x

    dct['extras'] = dict(ctx.extras)
    dct['extras'].update(other.extras)

    return dataclasses.replace(ctx, **dct)


@dataclasses.dataclass(slots=True)
class Context:
    pwd: bytes | None = None

    # EOCD Scan
    eocd_scan_initial_chunk_size: int = 256
    eocd_scan_max_retry: int = 4
    eocd_scan_rechunk_factor: int | float = 1
    eocd_scan_use_cache: bool = True

    # Extras
    extras: dict = dataclasses.field(default_factory=dict)

    # Helpers

    def __setattr__(self, name, value):
        try:
            super(Context, self).__setattr__(name, value)
        except AttributeError:
            self.extras[name] = value

    def __getattr__(self, name):
        if name in self.extras:
            return self.extras[name]
        else:
            raise AttributeError(name)

    def __or__(self, other):
        if isinstance(other, Context):
            return _merge(self, other)

        elif isinstance(other, dict):
            other_extras = {
                k: v
                for k, v in other.items()
                if k not in self.extras
            }

            extras = dict(self.extras)
            extras.update(other_extras)

            other_normals = {
                k: v
                for k, v in other.items()
                if k in self.extras
            }

            return dataclasses.replace(
                self, **other_normals, extras=extras
            )

        else:
            return NotImplemented


_context_fields = set(dataclasses.fields(Context))
