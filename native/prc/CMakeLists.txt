# BlackPDF - biblioteca nativa para leer PRC (blackprc).
#
# Compila nanoPRC (AGPL-3.0, CascadiaVoxel LLC) y zlib en una única
# biblioteca compartida junto con la capa blackprc.c, que es lo único que
# exporta. Las fuentes se descargan fijadas a un commit concreto; para usar
# una copia local:
#   -DFETCHCONTENT_SOURCE_DIR_NANOPRC=/ruta/a/nanoPRC
#   -DFETCHCONTENT_SOURCE_DIR_ZLIB=/ruta/a/zlib

cmake_minimum_required(VERSION 3.24)
project(blackprc C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
	set(CMAKE_BUILD_TYPE Release)
endif()

set(NANOPRC_COMMIT 4af181a4f645a5c854ac3ac72fe34f9012d7e041)
set(ZLIB_TAG v1.3.2)

include(FetchContent)
# SOURCE_SUBDIR apunta a una carpeta inexistente: solo queremos las fuentes,
# no los proyectos CMake originales (demos, SDL, tests).
FetchContent_Declare(nanoprc
	GIT_REPOSITORY https://github.com/mvrhel/nanoPRC.git
	GIT_TAG ${NANOPRC_COMMIT}
	GIT_SUBMODULES ""
	SOURCE_SUBDIR blackpdf-sources-only)
FetchContent_Declare(zlib
	GIT_REPOSITORY https://github.com/madler/zlib.git
	GIT_TAG ${ZLIB_TAG}
	GIT_SHALLOW TRUE
	SOURCE_SUBDIR blackpdf-sources-only)
FetchContent_MakeAvailable(nanoprc zlib)

set(NANOPRC_DIR ${nanoprc_SOURCE_DIR})
set(ZLIB_DIR ${zlib_SOURCE_DIR})

set(PRC_VERSION_STRING ${NANOPRC_COMMIT})
set(PRC_GIT_DESCRIBE ${NANOPRC_COMMIT})
configure_file(${NANOPRC_DIR}/include/prc_version.h.in ${CMAKE_CURRENT_BINARY_DIR}/generated/prc_version.h @ONLY)

set(NANOPRC_SOURCES
	prc_api.c prc_api_debug.c prc_api_write.c prc_api_write_pdf.c prc_style_api.c
	prc_tri_primitives_api.c prc_tri_primitives_helper_api.c prc_uncompressed_primitives_api.c
	prc_bit.c prc_bit_debug.c prc_write_bit.c prc_write_common.c prc_write_compress_tess.c
	prc_write_tess_3d.c prc_write_wire_tess.c prc_write_global.c prc_write_tree.c
	prc_write_file_structure.c prc_write_model.c prc_write_pdf.c prc_write_pdf_3d.c
	prc_context.c prc_diag_env.c prc_double.c prc_error.c prc_huff.c prc_memory.c
	prc_parse_common.c prc_parse_extra_geometry.c prc_parse_file_structure.c prc_parse_global.c
	prc_parse_main.c prc_parse_tess.c prc_parse_tree.c prc_pdf.c prc_pdf_dict.c prc_pdf_stream.c
	prc_pdf_decrypt.c prc_pdf_xref.c prc_release.c prc_schema.c prc_vector_util.c
	prc_decode_compressed_tess.c prc_decode_markup_tess.c prc_tessellation_exact_geom.c)
list(TRANSFORM NANOPRC_SOURCES PREPEND ${NANOPRC_DIR}/src/)

set(ZLIB_SOURCES
	adler32.c compress.c crc32.c deflate.c gzclose.c gzlib.c gzread.c gzwrite.c
	infback.c inffast.c inflate.c inftrees.c trees.c uncompr.c zutil.c)
list(TRANSFORM ZLIB_SOURCES PREPEND ${ZLIB_DIR}/)

add_library(blackprc SHARED blackprc.c ${NANOPRC_SOURCES} ${ZLIB_SOURCES})
target_include_directories(blackprc PRIVATE
	${NANOPRC_DIR}/include ${NANOPRC_DIR}/src ${CMAKE_CURRENT_BINARY_DIR}/generated ${ZLIB_DIR})
target_compile_definitions(blackprc PRIVATE
	PRC_LITTLE_ENDIAN
	PRC_VERSION_STRING="${PRC_VERSION_STRING}"
	PRC_DEBUG_MEMORY=0
	PRC_ENABLE_UNZIPPED_FUZZ=0
	PRC_ENABLE_DIAG_ENV=0)

include(CheckIncludeFile)
check_include_file(unistd.h HAVE_UNISTD_H)
if (HAVE_UNISTD_H)
	target_compile_definitions(blackprc PRIVATE HAVE_UNISTD_H)
endif()

if (MSVC)
	target_compile_definitions(blackprc PRIVATE _CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_DEPRECATE)
	# Runtime estático: la DLL no depende de vcruntime.
	set_property(TARGET blackprc PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
else()
	target_link_libraries(blackprc PRIVATE m)
endif()

if (WIN32)
	set_target_properties(blackprc PROPERTIES PREFIX "")
	if (MINGW)
		target_link_options(blackprc PRIVATE -static-libgcc -static)
	endif()
endif()
