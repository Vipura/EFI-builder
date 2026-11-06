bool XELP::decodeTGLGT2(IOService *device) {
    uint32_t val = read_sampler_slices(device);
    if (val != 8) return false;
    return true;
}
