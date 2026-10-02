// Binary to Gray and back: an assign in one module, a loop in the other.

module bin2gray (
    input  logic [3:0] bin,
    output logic [3:0] gray
);
  assign gray = bin ^ (bin >> 1);
endmodule

module gray2bin (
    input  logic [3:0] gray,
    output logic [3:0] bin
);
  always_comb begin
    for (int i = 0; i < 4; i++) bin[i] = ^(gray >> i);
  end
endmodule

module gray_code (
    input  logic [3:0] value,
    output logic [3:0] gray,
    output logic [3:0] round_trip
);
  bin2gray u_encode (.bin(value), .gray(gray));
  gray2bin u_decode (.gray(gray), .bin(round_trip));
endmodule
