from SCons.Builder import Builder
from SCons.Action import Action
from SCons.Errors import UserError

# from SCons.Scanner import C
from SCons.Script import Mkdir, Copy, Delete, Entry
from SCons.Util import LogicalLines

import os.path
import posixpath
import pathlib
import operator

from fbt.sdk import Sdk, SdkCache


def prebuild_sdk_emitter(target, source, env):
    target.append(env.ChangeFileExtension(target[0], ".d"))
    return target, source


def prebuild_sdk(source, target, env, for_signature):
    def _pregen_sdk_origin_file(source, target, env):
        mega_file = env.subst("${TARGET}.c", target=target[0])
        with open(mega_file, "wt") as sdk_c:
            sdk_c.write("\n".join(f"#include <{h.path}>" for h in env["SDK_HEADERS"]))

    return [
        _pregen_sdk_origin_file,
        "$CC -o $TARGET -E -P $CCFLAGS $_CCCOMCOM $SDK_PP_FLAGS -MMD ${TARGET}.c",
    ]


class SdkTreeBuilder:
    def __init__(self, env, target, source) -> None:
        self.env = env
        self.target = target
        self.source = source

        self.header_depends = []
        self.header_dirs = []

        self.target_sdk_dir = env.subst("f${TARGET_HW}_sdk")
        self.sdk_deploy_dir = target[0].Dir(self.target_sdk_dir)

    def _parse_sdk_depends(self):
        deps_file = self.source[0]
        with open(deps_file.path, "rt") as deps_f:
            lines = LogicalLines(deps_f).readlines()
            _, depends = lines[0].split(":", 1)
            self.header_depends = list(
                filter(lambda fname: fname.endswith(".h"), depends.split()),
            )
            self.header_dirs = sorted(
                set(map(os.path.normpath, map(os.path.dirname, self.header_depends)))
            )

    def _generate_sdk_meta(self):
        filtered_paths = [self.target_sdk_dir]
        # expanded_paths = self.env.subst(
        #     "$_CPPINCFLAGS",
        #     target=Entry("dummy"),
        # )
        # print(expanded_paths)
        full_fw_paths = list(
            map(
                os.path.normpath,
                (self.env.Dir(inc_dir).relpath for inc_dir in self.env["CPPPATH"]),
            )
        )

        sdk_dirs = ", ".join(f"'{dir}'" for dir in self.header_dirs)
        for dir in full_fw_paths:
            if dir in sdk_dirs:
                # print("approved", dir)
                filtered_paths.append(
                    posixpath.normpath(posixpath.join(self.target_sdk_dir, dir))
                )
            # else:
            # print("rejected", dir)

        sdk_env = self.env.Clone()
        sdk_env.Replace(CPPPATH=filtered_paths)
        with open(self.target[0].path, "wt") as f:
            cmdline_options = sdk_env.subst(
                "$CCFLAGS $_CCCOMCOM", target=Entry("dummy")
            )
            f.write(cmdline_options.replace("\\", "/"))
            f.write("\n")

    def _create_deploy_commands(self):
        dirs_to_create = set(
            self.sdk_deploy_dir.Dir(dirpath) for dirpath in self.header_dirs
        )
        actions = [
            Delete(self.sdk_deploy_dir),
            Mkdir(self.sdk_deploy_dir),
        ]
        actions += [Mkdir(d) for d in dirs_to_create]

        actions += [
            Copy(
                self.sdk_deploy_dir.File(h).path,
                h,
            )
            for h in self.header_depends
        ]
        return actions

    def generate_actions(self):
        self._parse_sdk_depends()
        self._generate_sdk_meta()

        return self._create_deploy_commands()


def deploy_sdk_tree(target, source, env, for_signature):
    if for_signature:
        return []

    sdk_tree = SdkTreeBuilder(env, target, source)
    return sdk_tree.generate_actions()


def gen_sdk_data(sdk_cache):
    api_def = []
    api_def.extend(
        (f"#include <{h.name}>" for h in sdk_cache.get_headers()),
    )
    api_def.append(
        "static const constexpr auto elf_api_table = sort(create_array_t<sym_entry>("
    )

    for fun_def in sdk_cache.get_functions():
        api_def.append(
            f"API_METHOD({fun_def.name}, {fun_def.returns}, ({fun_def.params})),"
        )

    for var_def in sdk_cache.get_variables():
        api_def.append(f"API_VARIABLE({var_def.name}, {var_def.var_type }),")

    api_def.append(");")
    return api_def


def validate_sdk_cache(source, target, env):
    print(f"Generating SDK for {source[0]} to {target[0]}")
    sdk = Sdk()
    sdk.process_source_file_for_sdk(source[0].path)
    for h in env["SDK_HEADERS"]:
        # print(f"{h.path=}")
        sdk.add_header_to_sdk(pathlib.Path(h.path).as_posix())

    sdk_cache = SdkCache(target[0].path)
    sdk_cache.validate_api(sdk.api_manager.api)
    sdk_cache.save_cache()


def generate_sdk_symbols(source, target, env):
    sdk_cache = SdkCache(source[0].path)
    if not sdk_cache.is_buildable():
        raise UserError("SDK version is not finalized, please run 'fbt sdk_check'")

    api_def = gen_sdk_data(sdk_cache)
    with open(target[0].path, "wt") as f:
        f.write("\n".join(api_def))


def generate(env, **kw):
    env.Append(
        BUILDERS={
            "SDKPrebuilder": Builder(
                emitter=prebuild_sdk_emitter,
                generator=prebuild_sdk,
                suffix=".i",
            ),
            "SDKTree": Builder(
                generator=deploy_sdk_tree,
                src_suffix=".d",
            ),
            "SDKSymUpdater": Builder(
                action=validate_sdk_cache,
                suffix=".csv",
                src_suffix=".i",
            ),
            "SDKSymGenerator": Builder(
                action=generate_sdk_symbols,
                suffix=".h",
                src_suffix=".csv",
            ),
        }
    )


def exists(env):
    return True