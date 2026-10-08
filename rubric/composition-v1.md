# Composition measures v1

Frozen 2026-10-08, before any picture was measured with them. Exploratory: not in the
pre-registration. Computed from the pixels by `adtone/composition.py`, with no reader. Any change to a
measure is v2, and measures from different versions are never pooled. `rubric/composition-v1.sha256`
holds this file's hash; the test suite fails if the file changes.

## What is measured, and why

How a picture is composed, in measures taken from the research on advertising and photographic
images, so that composition rests on the pixels as colour and light already do, whichever reader is
used. Each measure says where it comes from.

## The working picture

The readers' copy of the picture (at most 896 pixels on its longer side), converted to RGB and
resized with Lanczos filtering so that its longer side is 512 pixels. Y is its luminance, 0.299 R +
0.587 G + 0.114 B on a scale of 0 to 255. The saliency map is computed from Y reduced to 64 by 64
cells by averaging.

## The measures

- **complexity**: the working picture saved as a JPEG at quality 75 (Pillow's defaults otherwise),
  in bits per pixel. Pieters, Wedel and Batra (2010) measure an advertisement's feature complexity,
  the density of colour, luminance and edge detail, as its compressed file size; it hurts attention
  to the brand where design complexity helps attention to the picture.
- **edge_density**: the share of pixels where the Sobel gradient of Y (3 by 3 kernels, edges
  replicated) has a magnitude of at least 64. A second, simpler reading of the same clutter.
- **open_space**: the working picture is cut into blocks of 16 by 16 pixels (partial blocks at the
  edges left out); a block is plain when the standard deviation within it is below 4 for Y and for
  both opponent colour channels (R minus G, and the mean of R and G minus B). The share of plain
  blocks. After Pracejus, Olsen and O'Guinn (2006), for whom empty space of any colour signals
  prestige, quality and price.
- **symmetry**: the correlation between the 64 by 64 luminance grid and its mirror image, left to
  right: 1 for a picture symmetrical about its vertical centre line, near 0 for none. Symmetry and
  balance are among the composition features of computational aesthetics and of Zhang, Lee, Singh
  and Srinivasan (2022).
- **The saliency map**: the spectral residual of Hou and Zhang (2007). The two-dimensional Fourier
  transform of the 64 by 64 grid; the log amplitude less its average over each 3 by 3 neighbourhood
  (wrapping at the edges); transformed back with the original phase and squared; smoothed with a
  Gaussian with a standard deviation of 2.5 cells; scaled to sum to one. The salient region is the
  cells above three times the map's mean, Hou and Zhang's own threshold.
- **mass_x** and **mass_y**: the centre of the salient region, each cell weighted by the map (the
  whole map when no cell is salient), from 0 (left, top) to 1 (right, bottom). Where the picture's
  weight sits: left and right, top and bottom carry Kress and van Leeuwen's information value.
- **centre_offset**: the distance of that centre from the middle of the frame, divided by the
  distance from the middle to a corner: 0 in the middle, 1 in a corner.
- **thirds**: the distance of that centre from the nearest of the four points where the lines of
  thirds cross, divided by the largest such distance in the frame (a corner's): 0 on a point of
  thirds. The rule of thirds as a feature, after Datta, Joshi, Li and Wang (2006).
- **figure_size**: the share of the frame covered by the salient region; how much of the picture its
  subject takes up, a pixel reading of distance.
- **depth_of_field**: how much sharper the middle of the frame is than its surround: the base-2
  logarithm of the mean absolute Laplacian of Y (four neighbours) over the central quarter of the
  working picture (the middle half of its width and of its height), divided by its mean over the
  rest. 0 when they are equally sharp, positive when the middle is sharper; missing when either mean
  is zero. Datta and colleagues' low depth of field indicator, which sets the centre of the frame
  against the whole, in the form of a ratio.
- **diagonals**: among pixels whose Sobel magnitude is at least 64, the share of gradient magnitude
  whose direction lies within 22.5 degrees of a diagonal: 0 for a picture of only horizontal and
  vertical lines, about one half for lines in every direction. The dynamics of line direction in
  Machajdik and Hanbury (2010).
- **pleasure**, **arousal** and **dominance**: Valdez and Mehrabian's (1994) equations for the
  feelings colours evoke, from mean brightness B and mean saturation S of the working picture in HSV,
  each on a scale of 0 to 100: pleasure 0.69 B + 0.22 S, arousal minus 0.31 B + 0.60 S, dominance
  minus 0.76 B + 0.32 S. Machajdik and Hanbury use them the same way.

## Which measures go forward

Fixed with this file, before any picture was measured: on the bake-off's 296 pictures, a measure goes
forward when it varies (its interquartile range is above zero) and brands differ on it beyond chance:
the share of its variance that lies between brands, net of chance, with a permutation p below 0.05
after the Benjamini and Hochberg correction across these measures. A crop changes a picture's
composition, so how far two crops of one picture agree is reported for each measure, but it does not
decide. So is how far each agrees with the reader's answer to the matching question in tone-v2
(open_space; objects and arrangement; framing; placement), which is not a test of either.

## Sources

- Datta, R., Joshi, D., Li, J. and Wang, J. Z. (2006). Studying aesthetics in photographic images
  using a computational approach. *European Conference on Computer Vision*.
- Hou, X. and Zhang, L. (2007). Saliency detection: A spectral residual approach. *IEEE Conference on
  Computer Vision and Pattern Recognition*.
- Machajdik, J. and Hanbury, A. (2010). Affective image classification using features inspired by
  psychology and art theory. *ACM Multimedia*.
- Pieters, R., Wedel, M. and Batra, R. (2010). The stopping power of advertising: Measures and
  effects of visual complexity. *Journal of Marketing*, 74(5), 48 to 60.
- Pracejus, J. W., Olsen, G. D. and O'Guinn, T. C. (2006). How nothing became something: White space,
  rhetoric, history, and meaning. *Journal of Consumer Research*, 33(1), 82 to 90.
- Valdez, P. and Mehrabian, A. (1994). Effects of color on emotions. *Journal of Experimental
  Psychology: General*, 123(4), 394 to 409.
- Zhang, S., Lee, D., Singh, P. V. and Srinivasan, K. (2022). What makes a good image? Airbnb demand
  analytics leveraging interpretable image features. *Management Science*, 68(8), 5644 to 5666.
