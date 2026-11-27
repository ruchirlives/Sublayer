#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "mdl::mdl_core" for configuration "Release"
set_property(TARGET mdl::mdl_core APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdl_core PROPERTIES
  IMPORTED_COMMON_LANGUAGE_RUNTIME_RELEASE ""
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/libmdl_core.dll"
  )

list(APPEND _cmake_import_check_targets mdl::mdl_core )
list(APPEND _cmake_import_check_files_for_mdl::mdl_core "${_IMPORT_PREFIX}/bin/libmdl_core.dll" )

# Import target "mdl::mdlc" for configuration "Release"
set_property(TARGET mdl::mdlc APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdlc PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/mdlc.exe"
  )

list(APPEND _cmake_import_check_targets mdl::mdlc )
list(APPEND _cmake_import_check_files_for_mdl::mdlc "${_IMPORT_PREFIX}/bin/mdlc.exe" )

# Import target "mdl::mdl_sdk" for configuration "Release"
set_property(TARGET mdl::mdl_sdk APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdl_sdk PROPERTIES
  IMPORTED_COMMON_LANGUAGE_RUNTIME_RELEASE ""
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/libmdl_sdk.dll"
  )

list(APPEND _cmake_import_check_targets mdl::mdl_sdk )
list(APPEND _cmake_import_check_files_for_mdl::mdl_sdk "${_IMPORT_PREFIX}/bin/libmdl_sdk.dll" )

# Import target "mdl::i18n" for configuration "Release"
set_property(TARGET mdl::i18n APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::i18n PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/i18n.exe"
  )

list(APPEND _cmake_import_check_targets mdl::i18n )
list(APPEND _cmake_import_check_files_for_mdl::i18n "${_IMPORT_PREFIX}/bin/i18n.exe" )

# Import target "mdl::mdl_distiller_cli" for configuration "Release"
set_property(TARGET mdl::mdl_distiller_cli APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdl_distiller_cli PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/mdl_distiller_cli.exe"
  )

list(APPEND _cmake_import_check_targets mdl::mdl_distiller_cli )
list(APPEND _cmake_import_check_files_for_mdl::mdl_distiller_cli "${_IMPORT_PREFIX}/bin/mdl_distiller_cli.exe" )

# Import target "mdl::mdlm" for configuration "Release"
set_property(TARGET mdl::mdlm APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdlm PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/mdlm.exe"
  )

list(APPEND _cmake_import_check_targets mdl::mdlm )
list(APPEND _cmake_import_check_files_for_mdl::mdlm "${_IMPORT_PREFIX}/bin/mdlm.exe" )

# Import target "mdl::mdltlc" for configuration "Release"
set_property(TARGET mdl::mdltlc APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdltlc PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/mdltlc.exe"
  )

list(APPEND _cmake_import_check_targets mdl::mdltlc )
list(APPEND _cmake_import_check_files_for_mdl::mdltlc "${_IMPORT_PREFIX}/bin/mdltlc.exe" )

# Import target "mdl::dds" for configuration "Release"
set_property(TARGET mdl::dds APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::dds PROPERTIES
  IMPORTED_COMMON_LANGUAGE_RUNTIME_RELEASE ""
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/dds.dll"
  )

list(APPEND _cmake_import_check_targets mdl::dds )
list(APPEND _cmake_import_check_files_for_mdl::dds "${_IMPORT_PREFIX}/bin/dds.dll" )

# Import target "mdl::nv_openimageio" for configuration "Release"
set_property(TARGET mdl::nv_openimageio APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::nv_openimageio PROPERTIES
  IMPORTED_COMMON_LANGUAGE_RUNTIME_RELEASE ""
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/nv_openimageio.dll"
  )

list(APPEND _cmake_import_check_targets mdl::nv_openimageio )
list(APPEND _cmake_import_check_files_for_mdl::nv_openimageio "${_IMPORT_PREFIX}/bin/nv_openimageio.dll" )

# Import target "mdl::mdl_distiller" for configuration "Release"
set_property(TARGET mdl::mdl_distiller APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(mdl::mdl_distiller PROPERTIES
  IMPORTED_COMMON_LANGUAGE_RUNTIME_RELEASE ""
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/mdl_distiller.dll"
  )

list(APPEND _cmake_import_check_targets mdl::mdl_distiller )
list(APPEND _cmake_import_check_files_for_mdl::mdl_distiller "${_IMPORT_PREFIX}/bin/mdl_distiller.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
