# Copyright 2013, Big Switch Networks, Inc.
#
# LoxiGen is licensed under the Eclipse Public License, version 1.0 (EPL), with
# the following special exception:
#
# LOXI Exception
#
# As a special exception to the terms of the EPL, you may distribute libraries
# generated by LoxiGen (LoxiGen Libraries) under the terms of your choice, provided
# that copyright and licensing notices generated by LoxiGen are not altered or removed
# from the LoxiGen Libraries and the notice provided below is (i) included in
# the LoxiGen Libraries, if distributed in source code form and (ii) included in any
# documentation for the LoxiGen Libraries, if distributed in binary form.
#
# Notice: "Copyright 2013, Big Switch Networks, Inc. This library was generated by the LoxiGen Compiler."
#
# You may not use this file except in compliance with the EPL or LOXI Exception. You may obtain
# a copy of the EPL at:
#
# http://www.eclipse.org/legal/epl-v10.html
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# EPL for the specific language governing permissions and limitations
# under the EPL.

"""
Code generation

These functions extract data from the IR and render templates with it.
"""

from collections import namedtuple
from itertools import groupby
from StringIO import StringIO
import template_utils
from generic_utils import chunks
import loxi_globals
import loxi_ir.ir as ir
import util
import c_code_gen
import c_gen.of_g_legacy as of_g
import c_gen.type_maps as type_maps
import c_gen.c_type_maps as c_type_maps

CLASS_CHUNK_SIZE = 32

PushWireTypesData = namedtuple('PushWireTypesData',
    ['class_name', 'versioned_type_members'])
PushWireTypesMember = namedtuple('PushWireTypesMember',
    ['name', 'offset', 'length', 'value'])

def push_wire_types_data(uclass):
    if uclass.virtual or not uclass.has_type_members:
        return None

    # Generate a dict of version -> list of PushWireTypesMember
    type_members_by_version = {}
    for version, ofclass in sorted(uclass.version_classes.items()):
        pwtms = []
        for m in ofclass.members:
            if isinstance(m, ir.OFTypeMember):
                if m.name == "version" and m.value == version.wire_version:
                    # Special case for version
                    pwtms.append(PushWireTypesMember(m.name, m.offset, m.length, "obj->version"))
                else:
                    pwtms.append(PushWireTypesMember(m.name, m.offset, m.length, hex(m.value)))
        type_members_by_version[version] = pwtms

    # Merge versions with identical type members
    all_versions = sorted(type_members_by_version.keys())
    versioned_type_members = []
    for pwtms, versions in groupby(all_versions, type_members_by_version.get):
        versioned_type_members.append((pwtms, list(versions)))

    return PushWireTypesData(
        class_name=uclass.name,
        versioned_type_members=versioned_type_members)

ParseWireTypesData = namedtuple('ParseWireTypesData',
    ['class_name', 'versioned'])
ParseWireTypesVersion = namedtuple('ParseWireTypesVersion',
    ['discriminator', 'subclasses'])
ParseWireTypesSubclass = namedtuple('ParseWireTypesSubclass',
    ['class_name', 'value', 'virtual'])

def parse_wire_types_data(uclass):
    if not uclass.virtual:
        return None

    # Generate a dict of version -> ParseWireTypesVersion
    versioned = {}
    for version, ofclass in sorted(uclass.version_classes.items()):
        discriminator = ofclass.discriminator
        subclasses = [ParseWireTypesSubclass(class_name=subclass.name,
                                             value=subclass.member_by_name(discriminator.name).value,
                                             virtual=subclass.virtual)
                      for subclass in ofclass.protocol.classes if subclass.superclass and subclass.superclass.name == ofclass.name]

        subclasses.sort(key=lambda x: x.value)
        versioned[version] = ParseWireTypesVersion(discriminator=discriminator,
                                                   subclasses=subclasses)

    return ParseWireTypesData(class_name=uclass.name,
                              versioned=sorted(versioned.items()))

# Output multiple LOCI classes into each C file. This reduces the overhead of
# parsing header files, which takes longer than compiling the actual code
# for many classes. It also reduces the compiled code size.
def generate_classes(install_dir):
    for i, chunk in enumerate(chunks(loxi_globals.unified.classes, CLASS_CHUNK_SIZE)):
        with template_utils.open_output(install_dir, "loci/src/class%02d.c" % i) as out:
            for uclass in chunk:
                util.render_template(out, "class.c",
                    push_wire_types_data=push_wire_types_data(uclass),
                    parse_wire_types_data=parse_wire_types_data(uclass))
                # Append legacy generated code
                c_code_gen.gen_new_function_definitions(out, uclass.name)
                c_code_gen.gen_accessor_definitions(out, uclass.name)

# TODO remove header classes and use the corresponding class instead
def generate_header_classes(install_dir):
    for cls in of_g.standard_class_order:
        if cls.find("_header") < 0:
            continue
        with template_utils.open_output(install_dir, "loci/src/%s.c" % cls) as out:
            util.render_template(out, "class.c",
                push_wire_types_data=None,
                parse_wire_types_data=None)
            # Append legacy generated code
            c_code_gen.gen_new_function_definitions(out, cls)
            c_code_gen.gen_accessor_definitions(out, cls)

def generate_classes_header(install_dir):
    # Collect legacy code
    tmp = StringIO()
    c_code_gen.gen_struct_typedefs(tmp)
    c_code_gen.gen_new_function_declarations(tmp)
    c_code_gen.gen_accessor_declarations(tmp)
    c_code_gen.gen_generics(tmp)

    with template_utils.open_output(install_dir, "loci/inc/loci/loci_classes.h") as out:
        util.render_template(out, "loci_classes.h",
            legacy_code=tmp.getvalue())

def generate_lists(install_dir):
    for cls in of_g.ordered_list_objects:
        with template_utils.open_output(install_dir, "loci/src/%s.c" % cls) as out:
            util.render_template(out, "class.c",
                push_wire_types_data=None,
                parse_wire_types_data=None)
            # Append legacy generated code
            c_code_gen.gen_new_function_definitions(out, cls)
            c_code_gen.gen_list_accessors(out, cls)

def generate_strings(install_dir):
    object_id_strs = []
    object_id_strs.append("of_object")
    object_id_strs.extend(of_g.ordered_messages)
    object_id_strs.extend(of_g.ordered_non_messages)
    object_id_strs.extend(of_g.ordered_list_objects)
    object_id_strs.extend(of_g.ordered_pseudo_objects)
    object_id_strs.append("of_unknown_object")

    with template_utils.open_output(install_dir, "loci/src/loci_strings.c") as out:
        util.render_template(out, "loci_strings.c", object_id_strs=object_id_strs)

def generate_init_map(install_dir):
    with template_utils.open_output(install_dir, "loci/src/loci_init_map.c") as out:
        util.render_template(out, "loci_init_map.c", classes=of_g.standard_class_order)

def generate_type_maps(install_dir):
    # Collect legacy code
    tmp = StringIO()
    c_type_maps.gen_type_to_obj_map_functions(tmp)
    c_type_maps.gen_type_maps(tmp)
    c_type_maps.gen_length_array(tmp)
    c_type_maps.gen_extra_length_array(tmp)

    with template_utils.open_output(install_dir, "loci/src/of_type_maps.c") as out:
        util.render_template(out, "of_type_maps.c", legacy_code=tmp.getvalue())

ClassMetadata = namedtuple('ClassMetadata',
    ['name', 'wire_length_get', 'wire_length_set', 'wire_type_get', 'wire_type_set'])

def generate_class_metadata(install_dir):
    with template_utils.open_output(install_dir, "loci/inc/loci/loci_class_metadata.h") as out:
        util.render_template(out, "loci_class_metadata.h")

    with template_utils.open_output(install_dir, "loci/src/loci_class_metadata.c") as out:
        class_metadata = []
        for uclass in loxi_globals.unified.classes:
            wire_length_get = 'NULL'
            wire_length_set = 'NULL'
            wire_type_get = 'NULL'
            wire_type_set = 'NULL'

            if uclass and not uclass.virtual and uclass.has_type_members:
                wire_type_set = '%s_push_wire_types' % uclass.name

            if uclass.is_message and uclass.name != "of_header":
                wire_length_get = 'of_object_message_wire_length_get'
                wire_length_set = 'of_object_message_wire_length_set'
            elif uclass.is_action:
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_action_wire_object_id_get'
            elif uclass.is_instanceof('of_bsn_vport'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_bsn_vport_wire_object_id_get'
            elif uclass.is_action_id:
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_action_id_wire_object_id_get'
            elif uclass.is_instruction:
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_instruction_wire_object_id_get'
            elif uclass.is_instanceof('of_instruction_id'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_instruction_id_wire_object_id_get'
            elif uclass.is_instanceof('of_queue_prop'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_queue_prop_wire_object_id_get'
            elif uclass.is_instanceof('of_table_feature_prop'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_table_feature_prop_wire_object_id_get'
            elif uclass.is_instanceof('of_meter_band'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_meter_band_wire_object_id_get'
            elif uclass.is_instanceof('of_hello_elem'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_hello_elem_wire_object_id_get'
            elif uclass.is_instanceof('of_bsn_tlv'):
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_get = 'of_bsn_tlv_wire_object_id_get'
            elif uclass.is_oxm:
                wire_length_get = 'of_oxm_wire_length_get'
                wire_type_get = 'of_oxm_wire_object_id_get'
            elif uclass.name == "of_packet_queue":
                wire_length_get = 'of_packet_queue_wire_length_get'
                wire_length_set = 'of_packet_queue_wire_length_set'
            elif uclass.name == "of_meter_stats":
                wire_length_get = 'of_meter_stats_wire_length_get'
                wire_length_set = 'of_meter_stats_wire_length_set'
            elif uclass.name in ["of_group_desc_stats_entry", "of_group_stats_entry",
                   "of_flow_stats_entry", "of_bucket", "of_table_features",
                   "of_bsn_port_counter_stats_entry", "of_bsn_vlan_counter_stats_entry",
                   "of_bsn_gentable_entry_desc_stats_entry", "of_bsn_gentable_entry_stats_entry",
                   "of_bsn_gentable_desc_stats_entry"]:
                wire_length_get = "of_u16_len_wire_length_get"
                wire_length_set = "of_u16_len_wire_length_set"
            elif uclass.name == 'of_match_v3':
                wire_length_set = 'of_tlv16_wire_length_set'
                wire_length_get = 'of_tlv16_wire_length_get'
                wire_type_set = 'of_match_v3_push_wire_types'

            class_metadata.append(ClassMetadata(
                name=uclass.name,
                wire_length_get=wire_length_get,
                wire_length_set=wire_length_set,
                wire_type_get=wire_type_get,
                wire_type_set=wire_type_set))

        class_metadata.extend([
            ClassMetadata(
                name="of_action_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_action_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_action_id_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_action_id_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_bsn_vport_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_bsn_vport_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_instruction_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_instruction_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_instruction_id_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_instruction_id_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_queue_prop_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_queue_prop_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_table_feature_prop_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_table_feature_prop_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_meter_band_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_meter_band_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_hello_elem_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_hello_elem_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_bsn_tlv_header",
                wire_length_set='of_tlv16_wire_length_set',
                wire_length_get='of_tlv16_wire_length_get',
                wire_type_get='of_bsn_tlv_wire_object_id_get',
                wire_type_set='NULL'),
            ClassMetadata(
                name="of_oxm_header",
                wire_length_set='NULL',
                wire_length_get='of_oxm_wire_length_get',
                wire_type_get='of_oxm_wire_object_id_get',
                wire_type_set='NULL'),
        ])

        util.render_template(out, "loci_class_metadata.c", class_metadata=class_metadata)
