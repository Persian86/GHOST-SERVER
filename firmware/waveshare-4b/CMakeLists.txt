cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(ghost_room_4b VERSION 0.3.13)
# ESP-SR normally builds a separately flashed model image. Ghost embeds its
# model in ota_0 and our installer never writes the original model partition.
if(TARGET srmodels_bin)
    set_target_properties(srmodels_bin PROPERTIES EXCLUDE_FROM_ALL TRUE)
endif()
